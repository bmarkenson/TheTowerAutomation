# Utility Dissonance production confirmation — 2026-08-07

This narrow extract preserves the production evidence used to close
`ISSUE-2026-032`. Runtime logs and local battle records are subject to rolling
retention; the values below are historical evidence, not current-runtime
guidance.

## Confirmation boundary

- Production implementation `17e4e0c` was deployed through merge `7029456`;
  deployment documentation was merged through `c862ab4`.
- Production automation PID `360077`, runtime
  `5d92dbe088694fbc8c3a4b5bba69a3ea`, held the exact
  `localhost:5555` target lock. The confirmed battle scope was
  `55af48f2767e437b972deb08f11516e4`.
- The operator started the Tier 19 Utility Dissonance battle while automation
  was Paused. Strategy `none` was adopted for the active battle before Resume,
  so the deployed No Strategy observer owned only its declared inventory
  actions. After that inventory completed, automation was Paused again and the
  operator performed Surrender. Automation observed the natural Game Over
  boundary and handled the terminal record only after Resume.

## Badge observation

Two complete 1080x1920 `RUNNING / ATTACK_MENU` frames showed the localized
purple badge and white star beside `Tier 19`. The deployed detector reported:

| Signal | Value |
| --- | ---: |
| Dissonance family observed | `true` |
| Subtype / label | `Utility` / `Utility Dissonance` |
| Purple pixels | 1,061 |
| White icon pixels | 273–298 |
| White icon contour area | 205.5 |
| Attack symbol distance | 1.009491 |
| Defense symbol distance | 0.255085 |
| Ultimate Weapons symbol distance | 0.288496 |
| Utility symbol distance | 0.130096 |
| Nearest-shape margin | 0.124989 |
| Configured maximum distance / minimum margin | 0.2 / 0.05 |

The Utility distance and margin both passed their configured boundaries. The
same observation left the Attack menu accessible, rather than applying the
Attack-Dissonance constraint.

## Guarded inventory and terminal record

The bounded production action-log window included these decisive rows:

```text
[STATE 2026-08-07 01:13:13] UI state change: HOME_SCREEN → RUNNING
[INFO 2026-08-07 01:15:41] [CTRL] Adopted strategy none for active battle; startup gates deferred until the next run boundary
[RESULT 2026-08-07 01:17:02] Attached battle baseline recorded from the guarded player save — latest completed battle is Tier 19, wave 930
[INFO 2026-08-07 01:17:02] [NO_STRATEGY] Applied guarded attachment save observations for 12 fields: cards_deck, workshop_preset, free_upgrade_locks, bots_preset, guardian_chips, modules, target_priority, auto_pick_perks, perk_first_choice, perk_bans, perk_auto_pick_order, ultimate_weapons
[ACTION 2026-08-07 01:17:12] Collecting unresolved No Strategy configuration — record actual battle settings while visiting only fields not already resolved by guarded save or passive evidence
[INPUT 2026-08-07 01:17:25] Swipe requested: Upgrade menu toward the top
[INPUT 2026-08-07 01:17:30] Tap requested: Damage adjuster (attack)
[INPUT 2026-08-07 01:17:38] Tap requested: Dismiss damage adjuster
[RESULT 2026-08-07 01:17:43] No Strategy in-battle inventory complete — visited only the remaining UI fields: damage_slider
[STATE 2026-08-07 01:18:30] UI state change: RUNNING → GAME_OVER
[INFO 2026-08-07 01:19:44] [BATTLE_STATS] Saved record: logs/battles/Battle20260807T011927-0700.json (view: logs/battles/Battle20260807T011927-0700.md)
[RESULT 2026-08-07 01:19:47] Finished-battle handling complete — stats saved; returned Home
[INFO 2026-08-07 01:20:21] [NO_STRATEGY] Post-run inventory complete; the next-battle path is released
```

`Battle20260807T011927-0700` records:

```json
{
  "battle_type": "dissonance",
  "battle_type_analysis": {
    "type": "dissonance",
    "label": "Utility Dissonance",
    "confidence": "high",
    "signals": [
      "terminal_state:GAME_OVER",
      "terminal_observation:tier_19",
      "observed_identity:utility_dissonance"
    ],
    "observed_tier": 19
  }
}
```

Its interpreted run configuration records `Damage Slider: Percent Of Enemy
Health 1E-19%` at 01:17:35 PDT. The exact player-save Battle History projection
records Tier 19, wave 410, and Killed By `Surrender`.

## Save-enum correlation

After verified Home finalization, two byte-identical, read-only reads from the
exact target produced this privacy-safe projection:

```json
{
  "byte_identical": true,
  "dataVersion": 9,
  "versionNumber": 1073,
  "saveRevision": 47732,
  "source_sha256": "0c030c603e0bebcc0e3ee77cc61ad82a102cee1a1e2be0ad016acf4f0de0be1e",
  "dissonanceActive": true,
  "dissonanceSelected": 3,
  "latest_completed": {
    "tier": 19,
    "wave": 410,
    "killedBy": 99,
    "dissonanceType": 3
  }
}
```

The UI-confirmed Utility star, active selector `3`, and completed type `3`
provide one cross-channel calibration for Utility Dissonance. These raw fields
remain outside the runtime validation allowlist: this evidence does not make
the save authoritative for subtype detection, and it does not assign semantics
to any other enum value.

## Read-only extraction

The badge frames were obtained through bounded exact-target screenshots after
host-backed evidence matched the PID, systemd `MainPID`, held ADB lock,
connected target, and fresh runtime observation. The terminal fields were
projected from the locally generated battle record. The post-run save was read
twice in memory and reduced to the fields above; no raw save or private account
content was retained.
