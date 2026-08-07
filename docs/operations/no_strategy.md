# No Strategy Observation Run

Use Strategy `none` only to discover and retain actual configuration rather
than assert a known profile. It runs general handlers but no Strategy upgrade
actions or startup/session gates, and never fills a missing value from Farm,
Tournament, Tier, or another expected profile.

Complete [`live_preflight.md`](../live_preflight.md) before a live observation.
Select Strategy `none`, then treat process lifecycle, action authority, and
battle intent separately. **Start Automation** launches Paused. From verified
Home `NEW_BATTLE`, select **Start Battle** and then explicitly **Automation
Enabled**; the normal new-run boundary still owns its gates even though the
Strategy itself declares none. Pause before manual navigation. Passive capture
may continue while Paused, but every automated input remains blocked.

For manual play, first use **Take Manual Control** and wait for its
acknowledged indefinite Pause. **Return Control** is not an alias for Enable:
it records fresh passive observation while remaining Paused, and a later
explicit Enable enters configuration reconciliation. The revision-28 feature
branch intentionally holds both Return reconciliation and **Attach to Battle**
at their save-validation boundaries while the separate save-freshness slice is
in progress. Do not use either pending workflow in a live observation run and
do not Stop/Start merely to infer attachment.

After the separate Return/Attach integration is complete, expect one guarded
active-battle save acquisition: the game is
briefly backgrounded to Android Home, two byte-identical save reads are taken,
and the same battle is restored and reverified. The save's newest completed
battle becomes or validates the continuity baseline. A `save_first` running
attachment never opens Battle History UI; an unusable save logs a deferred
result and retries without game-UI navigation. The same snapshot supplies
complete allowlisted configuration observations, so only unresolved sections
may be opened afterward. The future terminal policy does not repair or replace this
continuity step.

During `RUNNING`, the runtime owns one guarded read-only inventory across only
the fields not already resolved by the save: Cards, Perks, Ultimate Weapons,
Modules, Event/Bots, Guild/Guardians, Target Priority, and accessible Damage
Slider. Every source/destination transition is verified; Pause mid-pass sends
no cleanup input and Enable first restores a known screen. Attack Dissonance
and Utility Dissonance identities come from the localized purple badge beside
Tier: the validated white sword means Attack and the validated white star means
Utility. Purple family evidence without a recognized icon never invents a
subtype. Only the sword makes Damage Slider unavailable without probing the
disabled Attack menu; the Utility star leaves Damage Slider unresolved so the
normal guarded Attack read still runs. A fully mapped save plus the Attack
badge can therefore produce no in-game inventory navigation, while Utility
still requires any genuinely unresolved Attack-only field.

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
