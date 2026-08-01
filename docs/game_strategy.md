# Game Mechanics and Strategy Context

Use this guide when interpreting battle results or changing a Farm profile.
It records the game model needed to distinguish an actual strategy regression
from a different Tier, Battle Condition, perk timeline, or Damage Slider
experiment.

This is not a claim that every community term or current account setting is
universally optimal. Read each statement according to its evidence class:

- **Game rule:** externally documented behavior. Community-maintained sources
  can lag a game release, so recheck them after material version changes.
- **Repository policy:** configuration that automation currently observes,
  preserves, or enforces.
- **Account strategy:** the operator's intended use of this tower and its
  upgrades.
- **Working hypothesis:** a setting worth testing, not a canonical optimum.
- **Live observation:** a dated read from the game UI. Battle Condition
  Reduction and other account upgrades can make live effects differ from a
  public base table.

## Build archetypes

The most useful classification is how the tower expects to survive its terminal
waves, not whether it has spent anything at all on health.

| Archetype | Primary terminal-wave survival | Typical killing model | Interpretation |
| --- | --- | --- | --- |
| Effective Health (eHP) | Absorb or recover from hits through health, wall health, defense, regeneration, and related mitigation | Orbs, thorns, and any supporting damage | A tanking build. Failure usually means incoming damage exceeded effective survivability. |
| Glass Cannon (GC) | Kill, disable, or avoid lethal enemies before they land a meaningful hit | Tower and Ultimate Weapon damage plus crowd control | Damage, enemy-health reduction, and control are survival stats. A large health pool does not make the build Hybrid if it cannot tank the terminal threat. |
| Hybrid | Uses both damage/control and meaningful hit tolerance at the endpoint | Damage handles some threats while health/mitigation handles others | Hybrid describes the actual survival mechanism; it does not require equal investment in health and damage. |

The current high-Tier account strategy is **Glass Cannon with selective Hybrid
aspects**. Its working premise at the relevant T18/T19 endpoints is that enemy
damage outruns tankable health more severely than enemy health outruns available
tower damage. The main survival plan is therefore to lower enemy health, raise
tower damage, and control or kill threats before they hit.

The Hybrid element is narrower: some Fleet enemies have very low or indirect
attack damage, so the tower can sometimes tolerate their contact while it could
not tank an ordinary terminal-wave hit. Commander, Saboteur, and Overcharge
also have mechanics that make Fleet handling more complicated than merely
adding health. Do not downgrade damage or enemy-health perks on the assumption
that this is a conventional balanced Hybrid build.

## Defensive layers and survival-chain interpretation

Do not compare the top-bar enemy Attack value directly with tower Health and
conclude that every contact must be fatal. The account has several defensive
layers, and completed battle reports record evidence from more than one of
them. The following groups are an analysis model, not a claim about the game's
exact internal damage-resolution order:

| Layer | Current strategic role | Battle-report evidence |
| --- | --- | --- |
| Avoidance and control | Damage, target priority, slows, stuns, knockback, and other control kill or delay a threat before it resolves an attack. This is the GC build's primary defense. | Damage by source, enemies hit by source, enemies destroyed by source, and terminal enemy counts |
| Defense and damage reduction | Defense Percent can grow to prevent 98% of incoming damage. Chrono Field, Chain Thunder, modules, bots, and other applicable effects can provide additional mitigation. Do not assume that separately displayed reductions stack additively or in a particular order without current evidence. | Damage Blocked rows such as Defense %, Chrono Field, Chain Thunder, Flame Bot, Primordial Collapse, and Negative Mass Projector |
| Wall interception | The Wall can receive damage before the tower and can rebuild, creating another boundary between an enemy attack and tower death. Wall state and rebuild timing can matter more than its final cumulative damage. | Tower Damage Taken and Wall Damage Taken are separate rows; a retained activation frame may show the current Wall/rebuild state |
| Energy Shield | This account has three simultaneous Energy Shield charges. Charges can recharge, so `Hits Absorbed By Energy Shield` can exceed three over a complete run. | Hits Absorbed By Energy Shield |
| Death Defy | Death Defy is a probabilistic lethal-event escape, not a stable amount of effective health. A run with poor rolls can end much earlier under otherwise similar pressure. | Death Defy count |
| Recovery | Health regeneration, Wall regeneration, lifesteal, and Recovery Packages matter when a hit is survivable and there is time to recover before the next one. They do not make an arbitrarily large hit tankable. | Health Regenerated, Health Recovered by Packages, and Recovery Packages |
| Rechargeable or limited survival controls | Second Wind, Demon Mode, and Nuke can interrupt or survive a failure sequence. Their activation waves reveal when pressure crossed a threshold, but not which enemy created that pressure. | Second Wind, Demon Mode, and Nuke counts plus the runtime activation timeline |

At 98% Defense, the Defense layer alone reduces an applicable hit to 2% of its
pre-Defense value, a 50-fold reduction:

```text
post-Defense damage = incoming damage × (1 - 0.98)
                    = incoming damage × 0.02
```

That still is not a complete effective-damage formula. Other reductions may
apply, the Wall or an Energy Shield may intercept the event, Death Defy may
prevent a lethal result, and a survivable hit may be recovered afterward.
Conversely, some enemy mechanics may interact with those defenses differently.
Use live mechanics and the battle report instead of inventing one universal
multiplier.

These layers change how an early death should be read:

- `Killed By` identifies the final damaging enemy. It does not identify every
  enemy that consumed Wall uptime, Energy Shields, Death Defies, Second Wind,
  Demon Mode, or Nuke earlier in the survival chain.
- High Death Defy or Energy Shield counts indicate repeated lethal exposure;
  they do not prove that the underlying build was durable. Low Death Defy counts
  can make the same pressure end a run much sooner.
- A health-reducing perk is important only when it changes the outcome of a hit
  that remains tankable after the applicable defensive layers. It is not
  automatically the cause of a GC failure.
- Damage and enemy-health perks remain survival perks. Faster clearing prevents
  finite defensive charges and probabilistic escapes from being consumed.

### Overcharge as the selective-Hybrid example

Overcharge is a good example of why this account is GC with selective Hybrid
aspects. Under the version 28.3 mechanics, an Overcharge starts from the Tier's
wave-1 damage, has a strongly reduced early attack rate that rises through wave
4,000, and multiplies its damage by `1.4` after each successful hit. It can also
fire a Parting Shot when killed after entering tower range. These rules are
documented in the
[official version 28.3 patch notes](https://www.techtreegames.com/post/v28-3-patch-notes).

An early Overcharge hit can therefore remain tankable after Defense and the
other applicable layers even when an ordinary current-wave attack cannot.
Health may buy additional Overcharge hits, but damage and control determine how
many opportunities it gets to stack the multiplier. When an early survival
ability activates, distinguish these hypotheses:

1. an Overcharge survived long enough to stack otherwise-tankable hits;
2. generic GC damage was insufficient and several dangerous enemies
   accumulated;
3. the build created repeated lethal exposure and an unusually poor Death Defy
   sequence shortened the run.

The completed report gives the number of Overcharges encountered and the final
killer, but not incoming damage by enemy type. A preserved activation frame can
support an Overcharge hypothesis; the aggregate count alone cannot prove one.

## Damage Slider economics

The Damage Cap Slider limits damage to a displayed percentage of the current
wave's enemy health, up to the tower's natural maximum. Conceptually:

```text
effective damage = min(natural maximum damage,
                       slider percentage × current wave enemy health)
```

Because enemy wave health rises, the permitted damage rises automatically.
Eventually the calculated cap reaches the tower's natural damage and the slider
stops reducing damage. This makes a fixed slider value capable of being
economy-oriented early and non-binding later.

For this account, the economic purpose is to prevent early overkill long enough
for more enemies to receive the relevant Orb tag before they die. The additional
tagged enemies increase coin income. The same general timing principle can
apply to other coin-multiplier areas or effects, but the exact benefit depends
on the active account mechanics.

The tradeoff is survival. A lower value can improve coins per hour while the
tower still clears every dangerous enemy in time. Once it delays a required
kill, it can reduce crowd control, allow an enemy mechanic to resolve, or end
the battle early. More coins per minute just before an early death do not prove
that the lower value improved whole-run coins per hour.

### Current T19 slider context

- During the short T19 experiment ending near wave 3,500, the operator changed
  the slider around wave 3,400 to `1E-20%`. It was the first tested value that
  was still visibly reducing damage at that wave.
- That run is confounded: the lower cap plausibly caused the early death, so it
  cannot by itself establish that perks or Target Priority were worse.
- The next comparison value is `1E-19%`. This is a control choice, not a
  permanent optimum.
- The operator confirmed both anomalous 2026-07-29 T19 runs
  (`Battle20260729T084914-0700` and `Battle20260729T124104-0700`) used
  Damage Slider `100%`. Their records retain the then-current `preserve`
  policy rather than an observed slider value, so this is operator evidence,
  but it excludes a deliberately low cap as the explanation for those two
  runs' collapsed entity throughput. The complete evidence and current
  host/emulator scheduling hypothesis are retained in the
  [open issue](observed_issues.md#t19-farm-retained-near-normal-game-clock-speed-while-entity-throughput-collapsed).
- The T18 Farm profile currently enforces `1E-22%`; the T19 Farm profile now
  enforces the `1E-19%` comparison value. Those policies are repository facts,
  not evidence that either number is permanently optimal.
- The 2026-07-31 comparison did not support treating the −55% enemy-health
  tradeoff as the safety boundary. A `1E-19%` run survived to wave 4,534, while
  another ended at wave 2,053 despite acquiring the −44% enemy-speed and −55%
  enemy-health tradeoffs by waves 1,240 and 1,540. The short run entered its
  Smart Missile Barrage/Demon Mode/Nuke survival sequence at waves
  1,962/1,968/2,043 and Scatter ended it at 2,053. Its one Death Defy and nine
  Energy Shield hits, versus 40 and 52 in the longer run, make survival
  variance before the renewable ability loop stabilized a material confounder.
- An initial `1E-18%` cap changing to `1E-19%` after the −55% perk remains a
  possible experiment, but it would not have prevented that observed early
  death and should not be treated as the decided Tier 19 policy. The generic
  multiple-perk transition design and an observation-only bypass are retained
  in the
  [runtime backlog](backlog/runtime-and-validation.md#strategy-driven-damage-slider-schedule);
  the exact Tier 19 schedule remains an experiment choice, not current runtime
  behavior.
- The next uncontended comparison was directionally compatible with that
  experiment but did not establish causation. A mixed-speed `1E-19%` run never
  received the −55% tradeoff and ended at wave 2,437; the immediately following
  clean x6.3 run received −55% at scheduled wave 1,287 and reached wave 4,903.
  Because the earlier wave-2,053 run died despite already having −55%, the perk
  is neither necessary nor sufficient evidence of safety. The pair strengthens
  the case for a configurable A/B transition test, not for assuming its result.

For a useful comparison, record the initial slider value, every changed value
and wave, whether damage visibly changed, and the final value. A run with a
mid-battle change is an experiment, not a clean strategy baseline.

## Perks in a GC analysis

For this build, perks that reduce enemy health or increase tower damage can
directly change terminal-wave survival. Their absence, or receiving them much
later, is therefore a plausible cause of a lower wave count. Health, defense,
and regeneration perks are secondary unless they address a specific Fleet
contact or another identified survivable hit.

Apply two cautions when comparing completed battles:

1. Final perk presence is not perk timing. A key perk acquired at wave 700 and
   the same perk acquired at wave 3,000 do not produce equivalent runs.
2. A shorter battle naturally receives fewer total perk selections. Perks
   missing only after the shorter run's death are a consequence, not a cause.

The current Auto Pick order is owned by the shared Farm run profile and is
resolved into the generated strategy and each battle record. Use the recorded
order and acquired-perk evidence for the specific battle instead of
reconstructing it from memory.

## Heat, Overheat, and Tier comparisons

In this project, **Heat** refers to the Tier's Battle Conditions. **Overheat**
refers to conditions that activate or intensify at a later wave. Tier alone is
therefore insufficient context for a battle comparison.

### Tournament Battle Condition abbreviations

The game UI displays complete Battle Condition names, levels, and effective
descriptions. External tournament summaries may instead use the following
shorthand. Treat these codes as import aliases, not as a replacement for the
game-facing name:

| Code | Battle Condition |
| --- | --- |
| `AR` | Armored Enemies |
| `BOU` | Boss's Ultimate |
| `BU` | Basic's Ultimate |
| `DD` | Death Defy Down |
| `DR` | Death Ray Resistance |
| `EAS` | Enemy Attack Speed |
| `ES` | Energy Shields Down |
| `FU` | Fast's Ultimate |
| `KB` | Knockback Resistance |
| `MAE` | Mass Enforcement |
| `MB` | More Bosses |
| `ME` | More Enemies |
| `OR` | Orb Resistance |
| `PC` | Plasma Cannon Resistance |
| `PU` | Protector's Ultimate |
| `RU` | Ranged Ultimate |
| `SD` | Enemy Level Skip Decay |
| `SPD` | Enemy Speed |
| `SRM` | Enemy Level Skip Reduction - Multiply |
| `TR` | Thorns Resistance |
| `TU` | Tank's Ultimate |
| `UWD` | Ultimate Weapon Durations |

The aliases are context-sensitive. In particular, `FU` means Fast's Ultimate
here rather than Free Upgrades, and `SD` means Enemy Level Skip Decay rather
than the Space Displacer module. In the patch `0.28.0` Legend history supplied
by the operator, each row contains `MB`, either `DD` or `ES`, `SD`, `SRM`, and
five variable conditions. That nine-code list identifies the base Tournament
conditions; it does not encode the separate Overheat panel or the live
account-adjusted effect values.

The repository can acquire stronger evidence directly. The retained
[`active Tournament Heat fixture`](../test/fixtures/ui_state_20260714/active_tournament_heat_20260718.png)
proves that the in-battle panel exposes full names, levels, and effective
descriptions under separate Heat and Overheat tabs. The exact version-1073
`playerInfo.dat` mapping now binds the current Legend Tournament number and
league to the same-version deterministic generator. Its condition identities
matched Tournaments 271–287 and the current live panel, so those normalized
identities may be attached to Tournament records without navigating the panel.
The UI remains authoritative for account-adjusted effects, levels, activation
waves, unknown conditions, unvalidated leagues, and new versions; external
tournament sites remain supplemental.

The following table captures the material T18/T19 differences visible in the
current game and public references. Condition levels describe the Tier rule.
Displayed effect percentages or durations can be softened by this account's
Battle Condition Reduction upgrades, so the live Heat panel is authoritative
for the effective value in a current battle.

| Condition | T18 | T19 | Strategic significance |
| --- | ---: | ---: | --- |
| Orb Resistance | Level 90 | Level 95 | T19 constrains Orb damage more strongly. |
| Death Ray Resistance | Level 90 | Level 95 | T19 constrains Death Ray damage more strongly. |
| Thorns Resistance | Level 60 | Level 70 | Direct damage matters more as thorns lose effectiveness. |
| Plasma Cannon Resistance | Level 60 | Level 70 | Boss support from Plasma Cannon is further reduced. |
| Knockback Resistance | Level 70 | Level 80 | T19 gives less time to control approaching enemies. |
| Armored Enemies | Level 40 | Level 50 | T19 enemies block more initial hits before taking damage. |
| Protector Ultimate | Level 50 | Level 65 | T19 increases the importance of removing Protector immunity windows. |
| Tank Ultimate | Level 35 | Level 50 | T19 presents a stronger Tank-specific control problem. |
| Scatter, Ray, and Vampire Ultimates | Level 20 | Level 35 | Each Elite mechanic is more severe on T19. |
| Fast Ultimate | None | Level 20 | T19 Fasts can accelerate up to two nearby enemies. |
| Enemy Level Skip reduction | Subtract Level 25 | Subtract Level 40 | T19 removes more EHLS/EALS effectiveness. |
| Regular Fleet schedule | First wave 95; every 100 | First wave 45; every 50 | T19 begins earlier and has twice the regular Fleet frequency. |
| Skip Decay Overheat | Wave 10,750 | Wave 10,500 | The additional ELS decay begins 250 waves earlier on T19. |
| Additional Fleet Overheat | Wave 10,750; every 100 | Wave 10,500; every 100 | The bonus Fleet schedule begins 250 waves earlier on T19. |
| More Bosses | Active from wave 1; every 5 | Active from wave 1; every 5 | No Tier difference in this condition. |

The 2026-07-26 read-only T19 inspection confirmed Fast Ultimate, the
wave-10,500 Overheat boundary, and live account-adjusted effects that differ
from the public base table. The retained 2026-07-14 T18 traversal confirms the
wave-10,750 boundary. Re-read the live panels after an account reduction
upgrade or game update rather than copying an old effect percentage forward.

Fleet mechanics are version-sensitive. Version 28.3 made Fleets targetable,
changed their pathing and attack behavior, and reworked Overcharge damage.
Recheck the current game and release notes after an update instead of carrying
version 27 assumptions forward. Their special effects and unusual attack
values still explain why Fleet frequency matters to a
GC-with-selective-Hybrid-aspects build.

## Target Priority is Tier-specific

The repository currently enforces this T18 order:

1. Fleets
2. Boss
3. Elites
4. In Spotlight
5. Tank
6. Closest (Default)
7. Ranged
8. Protector
9. Fast
10. Basic

The T19 profile now enforces the order observed live on 2026-07-26 as the
operator's **working hypothesis**:

1. Fast
2. Protector
3. Fleets
4. Boss
5. Elites
6. In Spotlight
7. Tank
8. Closest (Default)
9. Ranged
10. Basic

Enforcement holds this variable constant across T19 trials; it does not make
the order a proven or canonical optimum.

There is a coherent rationale for testing this order: T19 adds Fast Ultimate,
its Protector Ultimate creates important immunity windows, and regular Fleets
arrive twice as often as on T18. That makes the order plausible; it does not
make it proven or canonical. Public Fleet descriptions also carry
version-specific uncertainty, so do not infer exact targeting behavior from a
label alone.

`In Spotlight` is also an economy priority. The Spotlight Coin Bonus applies
to enemies killed while they are in Spotlight, so directing eligible tower
fire there can increase the share of kills receiving that multiplier. This
account has unusually wide Spotlight coverage, however, and most enemies
already die inside a beam without making it the first target. Moving
`In Spotlight` from fourth on T18 to sixth on the current T19 hypothesis may
therefore have only a small marginal CPH cost. That is a hypothesis to measure,
not a reason to treat Spotlight as economically unimportant.

Target Priority also does not control every damage source. Measure the
incremental fraction of qualifying kills or whole-run CPH instead of assuming
that moving `In Spotlight` by two positions changes every kill. Its best
position remains a survival/economy tradeoff: prioritizing a beam is useful
only while Fast, Protector, Fleet, or another immediate threat can safely wait.

Evaluate a Target Priority change using repeated runs with the same Tier,
slider policy, perk order, and major loadout. Compare death cause, dangerous
enemy accumulation, wave count, total coins, real duration, and whole-run coins
per hour. If several variables changed, describe the result as suggestive
rather than causal.

## Dissonant Utility is a Tier-specific coin multiplier

Dissonant Runs permanently strengthen a selected category on the Tier where
the run was completed. The four columns in the Dissonant Boosts panel are
Attack, Defense, Utility, and Ultimate Weapons; **Utility is the third column
from the left and its reward is a coin multiplier**.

A Utility Dissonant run disables the Utility systems during that challenge,
including ordinary Cash and Coins, Packages, Free Upgrades, and Enemy Level
Skips. Its direct Utility boost scales nonlinearly with the best wave reached
and caps at `x3` at wave 5,000. Dissonant Echo contributions from other Tiers
are then added to the direct boost. The panel's `Boost + Echo` view is therefore
the relevant total when comparing ordinary Farm CPH.

The operator's 2026-07-26 screenshot shows:

| Tier | Best Utility Dissonant wave | Displayed Utility `Boost + Echo` | Status |
| --- | ---: | ---: | --- |
| T18 | 5,000 | `x4.58` | Direct Utility boost complete |
| T19 | 3,462 | `x3.67` | Direct Utility boost incomplete |

Holding every other coin factor constant, `4.58 / 3.67 = 1.248`: T18 currently
has about a **24.8% coin-multiplier advantage** from Utility Dissonance alone.
This affects CPH directly and should be normalized before attributing a T18/T19
income difference to perks, Spotlight priority, Damage Slider, or Tier
economics.

Using the documented wave curve, T19 wave 3,462 corresponds to approximately
`x2.05` of the direct `x3` Utility boost. If its Echo contribution stays
unchanged, reaching wave 5,000 would raise its displayed total from about
`x3.67` to about `x4.62`, a projected **25.9% increase** over its current
Utility multiplier. This projection is useful for planning, but the live
`Boost + Echo` value after completion remains authoritative.

The current battle record does not capture Dissonant Boosts. Until it does,
record the applicable Tier's four `Boost + Echo` values or retain a dated panel
capture whenever cross-Tier CPH is being analyzed.

## Tier choice is a multi-objective optimization

The purpose of the T19 Farm profile is not to beat T18 on coins alone. T18
currently has the completed Utility Dissonant advantage, but T19 can accelerate
other progression currencies—primarily Reroll Dice and module shards, with
Cells as an additional benefit.

Public references use both “Reroll Dice” and “reroll shards” for the reroll
currency. This repository reports combined `Reroll Dice/hour` from earned and
fetched rows, and reports total `Module Shards/hour` by summing Cannon, Armor,
Generator, and Core shards.

Several Tier rules explain the T19 incentive:

| Reward input | T18 | T19 | T19 advantage before run-length effects |
| --- | ---: | ---: | --- |
| Regular Fleet schedule | Every 100 waves from wave 95 | Every 50 waves from wave 45 | Twice as many scheduled regular Fleets per wave |
| Reroll reward per successful Fleet drop | 1,650 | 1,800 | 9.1% more per successful drop |
| Module shards per successful Fleet drop | 9 | 10 | 11.1% more per successful drop |
| Boss reroll reward | 75 | 80 | 6.7% more per successful drop |
| Base Cells per Elite, average | 15 | 17 | 13.3% more per Elite |

With the documented 80% Fleet reroll and 20% Fleet module-shard chances, the
regular schedule alone implies approximately:

```text
Fleet rerolls per wave: T19 / T18
  = (0.80 × 1,800 / 50) / (0.80 × 1,650 / 100)
  ≈ 2.18

Fleet module shards per wave: T19 / T18
  = (0.20 × 10 / 50) / (0.20 × 9 / 100)
  ≈ 2.22
```

Those are per-wave expectations, not guaranteed whole-run hourly gains.
Different wave rates, final waves, Fleet survival, Elite density, fetched
rewards, and restart overhead can change the realized result. The completed
battle report already derives the correct comparison outputs:

- Coins/hour
- combined Reroll Dice/hour
- total module Shards/hour
- Cells/hour

Do not label T19 “worse” merely because its CPH is lower. It is a different
point on the resource tradeoff: T18 is currently favored for coins, while T19
may be favored for Dice, module shards, and Cells. Choose between them according
to the current progression bottleneck, or report all four rates without
collapsing them into a single score.

Run duration is a fifth decision axis. The representative wave-10,249 T18 run
lasted `9h 43m 38s`, while the wave-5,217 T19 run lasted `4h 6m 7s`. Ignoring
the short boundary transition, that is approximately:

```text
T18 natural completions per day: 24 / 9.727 ≈ 2.47
T19 natural completions per day: 24 / 4.102 ≈ 5.85
Experiment-cycle advantage:      5.85 / 2.47 ≈ 2.37
```

The shorter T19 run therefore supplies roughly 2.37 times as many natural Game
Over boundaries, completed battle reports, and clean opportunities to change
one variable for the next run. It also makes tournament timing easier because
the account can reach Home naturally in a smaller scheduling window instead of
abandoning a long operator-owned battle.

More boundaries introduce additional setup/restart overhead and shorter runs
have more per-run reward variance. Compare real-hour rates after that overhead,
but retain **feedback cadence** and **tournament fit** as genuine benefits when
choosing a Farm Tier.

## Battle-comparison checklist

Before explaining a wave-count or coins-per-hour difference:

1. Confirm Tier, game version, Heat levels, live account-adjusted effects, and
   any Overheat boundary crossed.
2. Identify the build's terminal survival mechanism and the actual death
   threat. Do not analyze this account as generic eHP.
3. Reconstruct the defensive chain: compare Tower and Wall damage, Energy
   Shield absorptions, Death Defies, and Second Wind/Demon Mode/Nuke activation
   timing. Treat `Killed By` as the final blow, not the complete cause.
4. Compare the Tier's Dissonant Utility `Boost + Echo` before explaining CPH;
   it is a direct coin multiplier, not a minor configuration detail.
5. Compare Cards, Modules, Ultimate Weapon toggles, Orb distances, Target
   Priority, Spotlight coverage, and game speed.
6. Compare Auto Pick capacity and order, then distinguish perk timing from
   final perk presence.
7. Record Damage Slider values and change waves. Treat a binding late-run
   reduction as a strong early-death confounder.
8. Compare total coins divided by real elapsed hours. Use instantaneous coin
   rate only as a checkpoint, not the final CPH conclusion.
9. Compare Reroll Dice/hour, total module Shards/hour, and Cells/hour before
   judging which Tier is the better Farm for the current progression goal.
10. Compare real run duration, clean experiment cycles per day, and the ability
   to reach a natural boundary before the next Tournament.
11. Separate causes from consequences. Lower wave count itself reduces perk
   opportunities, total coin accumulation, and exposure to later Heat.

## Sources and repository authority

External references are community-maintained and should be checked against the
current live game after a release:

- [Tower Knowledge Hub: Damage Cap Slider](https://the-tower.notion.site/Damage-Cap-Slider-1dc91383b93f80d7a9aff56dac0c7e3d)
- [Tower Knowledge Hub: Battle Conditions](https://the-tower.notion.site/Battle-Conditions-1dd91383b93f80d0b7c7f8c0f88ca89b)
- [Community Legend Battle Condition abbreviation table](https://www.reddit.com/r/TheTowerGame/comments/1tqz8bm/probability_of_legends_battle_conditions/)
- [Tower Knowledge Hub: overall build guide](https://the-tower.notion.site/Guide-to-Overall-Builds-Strategies-24791383b93f80a6a157c6367c9bb6e5)
- [Tower Knowledge Hub: Effective Health](https://the-tower.notion.site/Effective-Health-eHP-1d291383b93f8095bef7e94b9b89cd03)
- [Tech Tree Games: version 28.3 patch notes](https://www.techtreegames.com/post/v28-3-patch-notes)
- [The Tower Wiki: Dissonance](https://the-tower-idle-tower-defense.fandom.com/wiki/Dissonance)
- [The Tower Wiki: Spotlight](https://the-tower-idle-tower-defense.fandom.com/wiki/Spotlight)
- [The Tower Wiki: Modules and boss reroll rewards](https://the-tower-idle-tower-defense.fandom.com/wiki/Modules)
- [The Tower Wiki: Elite Cells](https://the-tower-idle-tower-defense.fandom.com/wiki/Currency/Elite_Cells)
- [The Tower Wiki: Fleet enemies](https://the-tower-idle-tower-defense.fandom.com/wiki/Fleet_Enemies)
- [The Tower Wiki: version 27 history](https://the-tower-idle-tower-defense.fandom.com/wiki/Version_History/V0.27)

Current repository behavior is authoritative in:

- [`../config/strategies/farm_t18.source.yaml`](../config/strategies/farm_t18.source.yaml)
- [`../config/strategies/farm_t19.source.yaml`](../config/strategies/farm_t19.source.yaml)
- [`../config/run_profiles/farm.yaml`](../config/run_profiles/farm.yaml)
- [`../config/loadouts/target_priorities.yaml`](../config/loadouts/target_priorities.yaml)
- [`ui_state_traversal_2026-07-14.md`](ui_state_traversal_2026-07-14.md)
