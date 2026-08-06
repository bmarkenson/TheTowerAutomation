# No Strategy Observation Run

Use Strategy `none` only to discover and retain actual configuration rather
than assert a known profile. It runs general handlers but no Strategy upgrade
actions or startup/session gates, and never fills a missing value from Farm,
Tournament, Tier, or another expected profile.

Complete [`live_preflight.md`](../live_preflight.md) before a live observation.
Start or attach with Strategy `none`; Pause before manual navigation. Passive
capture may continue while Paused, but every automated input remains blocked.

During `RUNNING`, the runtime owns one guarded read-only inventory across
Cards, Perks, Ultimate Weapons, Modules, Event/Bots, Guild/Guardians, Target
Priority, and accessible Damage Slider. Every source/destination transition is
verified; Pause mid-pass sends no cleanup input and Resume first restores a
known screen. Attack Dissonance identity comes from its purple sword badge,
not Tier alone, and its disabled Attack menu leaves Damage Slider explicitly
unavailable.

At natural Game Over, keep the runtime resumed with an actionable terminal
direction. No Strategy always performs full structured capture and routes Home
for its post-run sequence:

1. Require verified Home `NEW_BATTLE` and read the three supported Workshop
   Free Upgrade locks without changing them.
2. Return Home, open Cards and the independently verified Perks control, then
   capture complete First Perk, Ban Perks, and Auto Pick lists.
3. Revalidate Home and atomically update the same Battle JSON/Markdown. Retain
   Perks pages under
   `logs/battle_observations/<battle-id>/perk_configuration/`.

Normal Home/start handling remains held until completion. Uncertain OCR stays
raw/pending; failure retries the bounded read-only stage rather than releasing
a new battle. Architecture and evidence fields are in
[`architecture/runtime.md`](../architecture/runtime.md#no-strategy-observation-profile).
