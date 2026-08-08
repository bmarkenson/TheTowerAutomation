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
explicit Enable enters configuration reconciliation. Running Return and
**Attach to Battle** each perform one guarded active-battle acquisition: the
game is briefly backgrounded to Android Home, stable exact-target reads are
taken, and the same battle is restored and reverified. Active-round identity
and the final activity scope are mandatory; an unusable save or restoration
loss never opens Battle History or configuration UI and leaves the workflow
failed/interrupted and Paused. Home New Return follows the same one-attempt
rule rather than repeating a blocked serializer on later heartbeats. The same snapshot supplies complete allowlisted
configuration observations, so only unresolved sections may be opened
afterward. The future terminal policy does not repair or replace this
continuity step.

Attachment is observation-only by default: the configured startup Strategy is
not silently applied to the existing battle. Remain on No Strategy to monitor
and collect, or use the separate active-battle Strategy action when explicitly
warranted. Strategy adoption never grants Surrender authority.

When taking manual control, choose whether a manual Surrender uses the default
minimal excluded record with no terminal UI or opts into full terminal
collection. Detection comes from the bound natural save; neither choice is a
Surrender command.

To turn a manually changed loadout into managed authoring data, use **Capture
current setup as…** at a verified supported boundary after explicitly enabling
the guarded capture hold. Review the fresh-save values and unresolved rows,
then save a new Module preset or inactive Strategy draft. Saving does not
select or apply it. Automation Paused reports that refresh is unavailable and
never substitutes a cached snapshot. A failed capture receipt is retried from
the exact process-local preview without a second serialization; any lost owner
or round contradiction remains Paused.

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
