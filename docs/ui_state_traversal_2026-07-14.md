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

The full Home traversal with no active battle is still deferred until the next
natural battle boundary. The base no-battle Home control was previously
validated as `NEW_BATTLE`, but its complete submenu traversal was not repeated
here because preserving the current battle was the hard safety boundary.

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

- Repeat the safe Home/submenu audit at a genuine no-battle boundary and save
  only screens whose presentation or behavior differs from the Resume context.
- Exercise a complete natural Game Over -> Retry boundary while the default GC
  strategy is running, as already listed in `PENDING_DEVELOPMENT.md`.
- Add explicit recovery policy for known non-running panels as part of the
  planned interruptible non-running recovery work. Recognition alone does not
  grant a handler tap authority.

