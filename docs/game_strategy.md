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

### Current experiment context

- During the short T19 experiment ending near wave 3,500, the operator changed
  the slider around wave 3,400 to `1E-20%`. It was the first tested value that
  was still visibly reducing damage at that wave.
- That run is confounded: the lower cap plausibly caused the early death, so it
  cannot by itself establish that perks or Target Priority were worse.
- The next comparison value is `1E-19%`. This is a control choice, not a
  permanent optimum.
- The T18 Farm profile currently enforces `1E-22%`; the experimental T19
  profile preserves the live value. Those policies are repository facts, not
  evidence that the same number is optimal on both Tiers.

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

Fleet documentation is explicitly provisional for version 27. It nevertheless
establishes why Fleet frequency matters to a GC-with-Hybrid-aspects build:
Fleets resist or ignore several instant-kill and control mechanics, while their
special effects and unusual attack values differ from ordinary enemies.

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

The T19 profile deliberately uses `preserve`, not `enforce`. The order observed
live on 2026-07-26 is the operator's **working hypothesis**:

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

There is a coherent rationale for testing this order: T19 adds Fast Ultimate,
its Protector Ultimate creates important immunity windows, and regular Fleets
arrive twice as often as on T18. That makes the order plausible; it does not
make it proven or canonical. Public Fleet descriptions also carry
version-specific uncertainty, so do not infer exact targeting behavior from a
label alone.

Evaluate a Target Priority change using repeated runs with the same Tier,
slider policy, perk order, and major loadout. Compare death cause, dangerous
enemy accumulation, wave count, total coins, real duration, and whole-run coins
per hour. If several variables changed, describe the result as suggestive
rather than causal.

## Battle-comparison checklist

Before explaining a wave-count or coins-per-hour difference:

1. Confirm Tier, game version, Heat levels, live account-adjusted effects, and
   any Overheat boundary crossed.
2. Identify the build's terminal survival mechanism and the actual death
   threat. Do not analyze this account as generic eHP.
3. Compare Cards, Modules, Ultimate Weapon toggles, Orb distances, Target
   Priority, and game speed.
4. Compare Auto Pick capacity and order, then distinguish perk timing from
   final perk presence.
5. Record Damage Slider values and change waves. Treat a binding late-run
   reduction as a strong early-death confounder.
6. Compare total coins divided by real elapsed hours. Use instantaneous coin
   rate only as a checkpoint, not the final CPH conclusion.
7. Separate causes from consequences. Lower wave count itself reduces perk
   opportunities, total coin accumulation, and exposure to later Heat.

## Sources and repository authority

External references are community-maintained and should be checked against the
current live game after a release:

- [Tower Knowledge Hub: Damage Cap Slider](https://the-tower.notion.site/Damage-Cap-Slider-1dc91383b93f80d7a9aff56dac0c7e3d)
- [Tower Knowledge Hub: Battle Conditions](https://the-tower.notion.site/Battle-Conditions-1dd91383b93f80d0b7c7f8c0f88ca89b)
- [Tower Knowledge Hub: overall build guide](https://the-tower.notion.site/Guide-to-Overall-Builds-Strategies-24791383b93f80a6a157c6367c9bb6e5)
- [Tower Knowledge Hub: Effective Health](https://the-tower.notion.site/Effective-Health-eHP-1d291383b93f8095bef7e94b9b89cd03)
- [The Tower Wiki: Fleet enemies](https://the-tower-idle-tower-defense.fandom.com/wiki/Fleet_Enemies)
- [The Tower Wiki: version 27 history](https://the-tower-idle-tower-defense.fandom.com/wiki/Version_History/V0.27)

Current repository behavior is authoritative in:

- [`../config/strategies/farm_t18.source.yaml`](../config/strategies/farm_t18.source.yaml)
- [`../config/strategies/farm_t19_experiment.source.yaml`](../config/strategies/farm_t19_experiment.source.yaml)
- [`../config/run_profiles/farm.yaml`](../config/run_profiles/farm.yaml)
- [`../config/loadouts/target_priorities.yaml`](../config/loadouts/target_priorities.yaml)
- [`ui_state_traversal_2026-07-14.md`](ui_state_traversal_2026-07-14.md)
