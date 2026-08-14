# Home Perk repair production confirmation — 2026-08-08–13

This narrow extract preserves the later production evidence for
`ISSUE-2026-033`. The source action log is subject to rolling retention; these
rows and projections are historical evidence, not current-runtime guidance.

## Code boundary

- Fix-forward commit `f747515` was committed on 2026-08-07 at 21:48 PDT, and
  deployment record `08745f5` followed at 22:00 PDT. The successful repair
  below began on 2026-08-08 at 12:47 PDT.
- A read-only 2026-08-14 Git audit proved `08745f5` is an ancestor of production
  `main`. Only `eb8a391` subsequently changed
  `core/home_perk_configuration.py` or its focused test file; that change added
  pre-mutation save-mapping observations without replacing the guarded Ban or
  Auto Pick repair algorithms.

## Live Auto Pick repair

The decisive production action-log window was
2026-08-08 12:47:51–12:51:03 PDT:

```text
[DEBUG 2026-08-08 12:47:51] [HOME_PERKS] Verified Auto Pick order differs from the strategy; starting guarded repair
[INPUT 2026-08-08 12:48:20] Tap requested: Home preflight (auto pick move up:tower damage boss health tradeoff)
[DEBUG 2026-08-08 12:48:25] [HOME_PERKS] Auto Pick locally verified one upward swap; perk=Tower Damage / Boss Health Trade-Off rank_estimate=14->13 displaced=Ranged Distance / Ranged Damage Trade-Off
[INPUT 2026-08-08 12:48:34] Tap requested: Home preflight (auto pick move up:tower damage boss health tradeoff)
[DEBUG 2026-08-08 12:48:39] [HOME_PERKS] Auto Pick locally verified one upward swap; perk=Tower Damage / Boss Health Trade-Off rank_estimate=13->12 displaced=Boss Health / Boss Speed Trade-Off
[INPUT 2026-08-08 12:48:47] Tap requested: Home preflight (auto pick move up:tower damage boss health tradeoff)
[DEBUG 2026-08-08 12:48:52] [HOME_PERKS] Auto Pick locally verified one upward swap; perk=Tower Damage / Boss Health Trade-Off rank_estimate=12->11 displaced=Enemy Speed / Enemy Damage Trade-Off
[INFO 2026-08-08 12:49:47] [HOME_PREFLIGHT] Repair completed; Auto Pick priority restored
[RESULT 2026-08-08 12:51:03] Home-only run configuration complete — verified; repairs applied: Auto Pick priority restored; Module loadout restored (cannon_assist=Being Annihilator)
```

The associated diagnostic classified the pre-repair save as `save_mismatch`
and the final UI evidence as `ui_verified_repair`, without promoting stale
save carry.

The omitted final-readback row recorded identical expected and observed values
after correction. Its complete canonical order was:

```text
Perk Wave Requirement > Game Speed > Coin Trade-Off > Golden Tower Bonus >
Black Hole Duration > Death Wave Quantity > Coins Bonus > Orbs >
Free Upgrade Chance > Enemy Health / Tower Regen and Lifesteal Trade-Off >
Tower Damage / Boss Health Trade-Off > Enemy Speed / Enemy Damage Trade-Off >
Boss Health / Boss Speed Trade-Off >
Ranged Distance / Ranged Damage Trade-Off > Chain Lightning Damage >
Inner Land Mines > Spotlight Damage > Damage
```

## Later exact configuration matches

Two later Home preflights decoded `data-9-game-1101` with complete, supported
`save_match` evidence for both fields:

| Observed at (PDT) | Perk Bans | Auto Pick priority |
| --- | --- | --- |
| 2026-08-12 02:32:16 | Exact six-item match | Exact 18-row match |
| 2026-08-13 09:33:16 | Exact six-item match | Exact 18-row match |

The matched Ban order was:

```text
Lifesteal / Knockback Trade-Off > Enemies Damage / Tower Damage Trade-Off >
Defense Absolute > Interest > Land Mine Damage > Cash Bonus
```

The 2026-08-13 setup additionally reported `trusted_mismatches=[]` and
`ui_fallback=[]`, so neither result depended on a configuration-screen retry.

## Confirmation scope

This evidence confirms a natural, end-to-end Auto Pick correction and the
persistent desired outcome for both Perk fields. It does not claim that the
exact two ignored reverse swipes or the transient Ban deselection no-op
recurred after deployment. Those adverse branches remain covered by the exact
regressions in `test/test_home_perk_configuration.py`; manufacturing another
configuration mismatch is not a pending live-validation requirement.

## Read-only extraction

The rows were selected on 2026-08-14 from `logs/actions.log` with bounded line
ranges around the three timestamps and reduced to the fields above. Git
continuity was checked with read-only ancestry, path-log, and aggregate-diff
commands. No service, control, save, or device mutation was used for this
history update.
