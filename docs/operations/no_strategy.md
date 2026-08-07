# No Strategy Observation Run

Use Strategy `none` only to discover and retain actual configuration rather
than assert a known profile. It runs general handlers but no Strategy upgrade
actions or startup/session gates, and never fills a missing value from Farm,
Tournament, Tier, or another expected profile.

Complete [`live_preflight.md`](../live_preflight.md) before a live observation.
Start or attach with Strategy `none`; Pause before manual navigation. Passive
capture may continue while Paused, but every automated input remains blocked.
From verified Home `NEW_BATTLE`, the operator may start the battle manually
while Paused and then Resume after `RUNNING` is visible. The pending Home
continuity baseline follows that observed start. Do not Stop/Start merely to
attach it: leave the runtime Paused during manual navigation, wait for passive
`RUNNING` status, then Resume. If a process replacement is otherwise needed,
the replacement uses the same attachment path.

After Resume, expect one guarded active-battle save acquisition: the game is
briefly backgrounded to Android Home, two byte-identical save reads are taken,
and the same battle is restored and reverified. The save's newest completed
battle becomes or validates the continuity baseline. A `save_first` running
attachment never opens Battle History UI; an unusable save logs a deferred
result and retries without game-UI navigation. The same snapshot supplies
complete allowlisted configuration observations, so only unresolved sections
may be opened afterward. Terminal mode does not repair or replace this
continuity step.

During `RUNNING`, the runtime owns one guarded read-only inventory across only
the fields not already resolved by the save: Cards, Perks, Ultimate Weapons,
Modules, Event/Bots, Guild/Guardians, Target Priority, and accessible Damage
Slider. Every source/destination transition is verified; Pause mid-pass sends
no cleanup input and Resume first restores a known screen. Attack Dissonance
identity comes from its purple sword badge, not Tier alone, and that passive
badge leaves its disabled Damage Slider explicitly unavailable without probing
the Attack menu. A fully mapped save plus that badge produces no in-game
inventory navigation.

At natural Game Over, keep the runtime resumed with an actionable terminal
direction. No Strategy always performs full structured capture and routes Home
for its post-run sequence:

1. Require verified Home `NEW_BATTLE`. Read Workshop and the three supported
   Free Upgrade locks only if the guarded save did not already resolve them.
2. Open Cards and the independently verified Perks control only for unresolved
   selected-deck, First Perk, Ban Perks, or Auto Pick fields.
3. At verified Home, atomically update the same Battle JSON/Markdown. A
   save-complete observation finalizes without either configuration traversal.
   Retain any required Perks pages under
   `logs/battle_observations/<battle-id>/perk_configuration/`.

Normal Home/start handling remains held until completion. Uncertain OCR stays
raw/pending; failure retries the bounded read-only stage rather than releasing
a new battle. Architecture and evidence fields are in
[`architecture/runtime.md`](../architecture/runtime.md#no-strategy-observation-profile).
