# No Strategy attachment promotion evidence — 2026-08-06

This narrow extract preserves the production evidence used to close
`ISSUE-2026-028` and `ISSUE-2026-029`. The source action log is subject to
rolling retention; the values below are not current-runtime guidance.

## Deployment boundary

- Production moved from `d5ff68d` to validated `develop` candidate `ab84a3c`.
- Annotated rollback tag
  `production-before-20260807T011012Z-d5ff68d` points to `d5ff68d`.
- The complete checkpoint at `ab84a3c` passed compilation, state definitions,
  clickmap integrity with zero errors and 44 established orphan candidates,
  and all 1,676 tests in 328.97 seconds.

## Save-only running attachment

The production action-log window was 2026-08-06 18:11:41–18:13:47 PDT:

```text
[INFO 2026-08-06 18:11:41] Exited cleanly.
[ACTION 2026-08-06 18:12:38] [CONTROL_SURFACE] Started automation service with state PAUSED and startup gates auto_validate using selected strategy none
[STATUS 2026-08-06 18:12:44] State=RUNNING/PAUSED | Wave=3089 | Coins/min=0 | Speed=x6.3
[ACTION 2026-08-06 18:12:59] Checking attached battle continuity — determine whether a battle completed while automation was stopped
[DEBUG 2026-08-06 18:12:59] [BATTLE_CONTINUITY] mode=compare channel=stable player save scope_id=be918bd7390b460983c7c7e6637c4f3a
[INPUT 2026-08-06 18:13:03] Backgrounding The Tower to Android Home from the attached running battle
[INPUT 2026-08-06 18:13:04] Restoring The Tower from Android Home to the attached running battle
[RESULT 2026-08-06 18:13:10] Attached battle continuity confirmed — UI identity corroborated the fresh save tail
[INFO 2026-08-06 18:13:10] [NO_STRATEGY] Applied guarded attachment save observations for 11 fields: cards_deck, workshop_preset, free_upgrade_locks, bots_preset, guardian_chips, target_priority, auto_pick_perks, perk_first_choice, perk_bans, perk_auto_pick_order, ultimate_weapons
[RESULT 2026-08-06 18:13:43] No Strategy in-battle inventory complete — visited only the remaining UI fields: modules
[STATUS 2026-08-06 18:13:47] State=RUNNING | Wave=3106 | Coins/min=0 | Speed=x6.3
```

No Battle History action or input occurred in that attachment window. The
retained terminal interactive-development lease remained released, and the
replacement emitted no abnormal lease warning, duplicate result, or directive
rewrite.

The privacy-safe player-save inspector was then run read-only against the exact
`localhost:5555` target after the known forced serialization. Its Module check
reported:

```json
{
  "status": "unmapped",
  "complete": false,
  "reason": "unsupported primary module infoIndex",
  "source_fields": ["moduleEquipped", "assistModuleSlots"]
}
```

The Module UI visit was therefore the documented fail-closed fallback for an
incomplete save check, not repetition of a save-resolved value. Mapping the new
primary Module index remains active work. The `visited only ... modules` result
does not establish that every other UI field was truly unavailable: the badge
was later established as Utility Dissonance, and the then-current purple-only
detector had incorrectly suppressed the accessible Damage Slider read. That
separate subtype repair is tracked by
[`ISSUE-2026-032`](../resolved-2026.md#utility-dissonance-star-was-labeled-as-attack).
This replacement did not reproduce the separate paused-Home manual-start
transition required to close `ISSUE-2026-027`.

## Read-only extraction

The action rows were selected from `logs/actions.log` by timestamp and component
after host-backed status proved the matching PID, systemd `MainPID`, held
`localhost:5555` lock, connected target, and fresh observation. The Module
result was projected without raw save contents by:

```bash
.venv/bin/python tools/import_player_save.py \
  --adb-target localhost:5555 --freshness-verified --compact \
  | jq '.checks.modules'
```
