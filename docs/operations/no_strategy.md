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
it records fresh visual observation while remaining Paused, and a later
explicit Enable enters configuration reconciliation. Running Return and
**Attach to Battle** each perform one guarded active-battle acquisition: the
game is briefly backgrounded to Android Home, stable exact-target reads are
taken, and the same battle is restored and reverified. Active-round identity
is mandatory and has no History/UI fallback. Activity scope is optional log
metadata. Once identity succeeds, unusable configuration projections may open
supported configuration discovery; attachment completes
observation-only with Automation Enabled so No Strategy monitoring and safe
collectors continue. Home New Return similarly runs the supported Home UI
checks, and Game Over Return runs the full terminal UI collector. Restoration,
owner, target, canonical identity, or authority loss is catastrophic and leaves input
Paused. A catastrophically unsafe Home serializer is terminalized once rather
than repeated on later heartbeats. Recoverable mismatch, unavailable evidence,
and exhausted repair are flagged while automation continues. The
future terminal policy does not repair or replace this identity step.

Selecting No Strategy before Attach deliberately requests observation-only
adoption and is not a degraded condition. Other selections use the
strategy-aware Attach contract: a compatible snapshotted Strategy becomes
active, while an incompatible or unprovable selection observes the current
battle as degraded and remains pending for the next safe boundary. Strategy
adoption never grants Surrender or current-battle repair authority.

When taking manual control, choose whether a save-confirmed manual Surrender
uses the default minimal excluded record with no terminal UI or opts into full
terminal collection. If the natural save is unusable, the minimal shortcut is
unavailable and the full terminal UI route is used. Neither choice is a
Surrender command.

To turn a manually changed loadout into managed authoring data, use **Capture
current setup as…** at a verified supported boundary after explicitly enabling
the guarded capture hold. Review the fresh-save values and unresolved rows,
then save a new Module preset or inactive Strategy draft. Saving does not
select or apply it. Automation Paused reports that refresh is unavailable and
never substitutes a cached snapshot. A failed capture receipt is retried from
the exact process-local preview without a second serialization. A safely
restored round contradiction fails that capture and releases its owner; lost
owner/target/source authority may Pause for safety. Capture has
no complete UI authoring equivalent; an unusable save reports the capture
unavailable without disabling the separate No Strategy UI monitors.

During `RUNNING`, the runtime owns one guarded read-only inventory across only
the fields not already resolved by the save: Cards, Perks, Ultimate Weapons,
Modules, Event/Bots, Guild/Guardians, Target Priority, and accessible Damage
Slider. A new inventory route is granted only from fresh `RUNNING` while the
battle lifecycle still owns that exact active battle. The synchronous route
may traverse its verified panels, but a Cards/Perks/Modules/Event/Guild panel
later observed by the main loop is never assumed to be automation-owned. This
prevents an old No Strategy collector from closing or navigating an
operator-opened Home panel. Every source/destination transition is verified;
Pause mid-pass sends no cleanup input. A later Enable does not infer cleanup
ownership from the panel; a new pass waits for fresh `RUNNING`. Attack
Dissonance
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

Uncertain OCR stays raw/pending. If an optional read or persistence stage still
fails after its bounded attempt, the runtime preserves the partial observation
with explicit unresolved evidence and releases verified Home; it does not
Pause or hold all future battles indefinitely. Continue selected for that
exact Game Over may carry one bound, one-shot New Battle launch through this
required Home inventory. Changing the future policy after the battle ended or
while already at Home does not create that launch; use **Start Battle** for an
immediate start. Only restoration to verified Home may remain pending, and
explicit `WAIT` continues to hold by operator choice. Architecture and
evidence fields are in
[`architecture/runtime.md`](../architecture/runtime.md#no-strategy-observation-profile).
