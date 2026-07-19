# UI state traversal — 2026-07-14

This is the durable record of the guided 1080x1920 traversal performed against
`localhost:5565`. The automation process remained authoritatively paused while
it was observing and was later stopped for stable direct captures. The active
battle was preserved throughout: only the guarded **Go Home** route was used,
and **Surrender** was never tapped.

Representative live captures are under
`screenshots/ui_traversal_2026-07-14/`. Canonical regression fixtures for the
implemented states are under `test/fixtures/ui_state_20260714/`.

## Completed contexts

- Active battle, including all four upgrade menus and the safe/read-only
  in-run side-menu destinations.
- Home while a battle remained active (`RESUME BATTLE`), including the bottom
  navigation and safe/read-only side-menu destinations.
- Store opened from Home with the Daily Gem cooldown already visible. This
  confirmed that Store retains its scroll position and that the handler should
  test for cooldown before scrolling.

At the time of this pass, the full Home traversal with no active battle was
deferred because preserving the current battle was the hard safety boundary.
That context was completed at the next authorized no-battle boundary on
2026-07-19, as recorded below.

## 2026-07-19 no-battle completion

The safe Home/submenu audit was repeated from fresh `HOME_SCREEN` plus
`NEW_BATTLE` evidence on `localhost:5555`. Workshop, Cards, Modules, Lab, Store,
Daily Missions, Event, Guild, Tournament, Settings, Ranking, Themes, Inbox, The
Vault, and Battle History retained their existing explicit states. Claims,
purchases, tournament entry, preset or loadout changes, and external links
remained excluded.

Two expected read-only screens initially resolved to `UNKNOWN`:

| Screen | Resulting primary state | Notes |
| --- | --- | --- |
| Home Perks configuration | `PERKS` | Added the uppercase configuration-title variant; First Perk, Ban Perks, and Auto Pick were not changed. |
| Milestones | `MILESTONES` | Added a dedicated primary state; no milestone reward or Tier selector was tapped. |

The Currencies popup opened from the global header and the Android Exit Game
confirmation also gained explicit `CURRENCIES_DIALOG` and `EXIT_GAME_DIALOG`
overlay states over their existing Tournament and Milestones primaries. The
Exit Game action itself was never selected.

All four observations have retained 2026-07-19 fixtures in
`test/fixtures/ui_state_20260714/`, dedicated templates, automated coverage in
`test/test_ui_state_coverage.py`, and live post-change classification evidence.

The same bounded pass then exercised the actual generated `farm` profile from a
verified Tier 18 `NEW_BATTLE` boundary. Home-only Farm setup passed, EHLS and
EALS completed at waves 20 and 30, Damage Slider verified `1E-22%`, Target
Priority matched the resolved Farm order, and the complete session preflight
passed before normal handlers resumed. The resolved Farm configuration was
also persisted in the resulting battle record.

## State results

The following existing states continued to classify correctly:

- `RUNNING` with Attack, Defense, Utility, and Ultimate Weapons menus;
- `HOME_SCREEN`, `WORKSHOP`, `CARDS`, `MODULES`, `LAB`, `STORE`,
  `DAILY_MISSIONS`, `SETTINGS`, `TARGET_PRIORITY`, `EVENT`, and `GUILD`;
- the existing Card preset, Event Bots, Workshop Farm, and Guild Guardian
  secondary evidence.

The traversal exposed and fixture-backed these formerly `UNKNOWN` screens:

| Screen or modal | Resulting primary state | Notes |
| --- | --- | --- |
| Wave Info and Stats tabs | `WAVE_PANEL` | One state covers both tabs. |
| Perks / Auto Pick panel | `PERKS` | Auto Pick was observed on and not toggled. |
| Settings Stats, Toggles, Language, Patch Notes, Credits, Encyclopedia | `SETTINGS` | Subpages inherit the existing parent state. |
| Select Research and Lab History | `LAB` | No research was selected or replaced. |
| Modules Information and History | `MODULES` | No module was equipped, merged, or shattered. |
| Battle Heat and Overheat tabs | `BATTLE_HEAT` | Both tabs are read-only. |
| Battle History and report detail | `BATTLE_HISTORY` | Report inspection only. |
| Distance Adjuster | `DISTANCE_ADJUSTER` | Refreshed the stale title geometry; no value changed. |
| Expanded buy quantity | `RUNNING` + `BUY_QUANTITY_MENU_EXPANDED` | Refreshed the stale expanded-row geometry. |
| Generic and Ultimate Weapon upgrade descriptions | `UPGRADE_DETAIL` | Generalized the former UW-only dismiss path. |
| Exit Battle confirmation | `EXIT_BATTLE_DIALOG` | Explicit state and overlay; no action was selected during capture. |
| Ranking | `RANKING` | Read-only modal over Home. |
| Inbox Mail and News | `INBOX` | No mail action was available. |
| Profile / Themes | `THEMES` | No profile, theme, or relic was edited. |
| The Vault Harmony and Power | `VAULT` | No node was unlocked. |
| Tournament and its read-only dialogs | `TOURNAMENT_SCREEN` | Information, history, and prizes were inspected; no tournament was entered. |

The specialized Damage popup remains `DAMAGE_ADJUSTER`. It may also carry the
general `UPGRADE_DETAIL` overlay, but the ordinary primary state takes
precedence over the fallback modal primary.

## Interaction findings

- Long-pressing a Card in the active deck or Inventory opens its description.
  The underlying Cards header remains visible, so it continues to classify as
  `CARDS`; no new primary was required.
- Pressing upgrade-card text in Attack, Defense, Utility, or Ultimate Weapons
  opens a description panel. The previous UW-only overlay was stale and too
  narrow, so the common dismiss handler now owns the generalized
  `UPGRADE_DETAIL` overlay.
- Pressing the Wave box opens the Wave Info / Stats panel.
- The purple progress bar opens Perks, selected perks, and Auto Pick Perks.
- Pressing a running Lab opens Select Research, where another research could
  replace it. The traversal stopped before any replacement choice.
- The three battle controls above the wall are Nuke, Demon Mode, and Missile
  Barrage. They are configured for automatic activation and were deliberately
  not pressed during traversal.

## Deliberately excluded actions

- Surrender, tournament entry, Nuke, Demon Mode, and Missile Barrage;
- claims, purchases, unlocks, rerolls, respecs, research replacement, preset
  changes, card/module/guardian equipment changes, and settings toggles;
- external Account, Discord, Subreddit, Support, EULA, and Privacy links;
- ad playback and any control whose only safe observation required spending or
  changing persistent game state.

## Remaining traversal work

- Continue the broader farm-lifecycle and transient-dialog audit tracked in
  `PENDING_DEVELOPMENT.md`; this pass deliberately stayed within the safe
  no-battle Home and initialization scope.
- Add explicit recovery policy for known non-running panels as part of the
  planned interruptible non-running recovery work. Recognition alone does not
  grant a handler tap authority.
