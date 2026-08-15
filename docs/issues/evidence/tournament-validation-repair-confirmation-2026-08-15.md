# Tournament validation repair confirmation — 2026-08-15

This narrow dated extract records the operator-authorized post-deployment
confirmation for `ISSUE-2026-001`, `ISSUE-2026-046`, `ISSUE-2026-048`, and
`ISSUE-2026-050`. It is historical evidence, not a statement about current
runtime state.

## Source and extraction

- Source: production `logs/actions.log` for runtime
  `e3187359db534df19d1a69da77beae1b`, exact target generation 1 on
  `localhost:5555`.
- Window: 2026-08-15 09:53:15 through 09:55:09 PDT.
- Read-only extraction: `sed -n '113942,114090p' logs/actions.log`, followed by
  selection of the rows below. The complete in-battle navigation inventory was
  independently checked with
  `sed -n '113979,114035p' logs/actions.log | rg '\[INPUT '`. Raw save data,
  decoded objects, account fields, Module identifiers, and individual Module
  assignment values were intentionally omitted.

## Retained rows

| PDT | Retained evidence |
| --- | --- |
| 09:53:15 | Save-first Home preflight completed from a trusted version-1101 snapshot. Cards, Workshop, Bots, Guardians, all eight Module assignments, Damage Slider, Poison Swamp Stun, Ultimate Weapon primaries, and Spotlight Missiles were accepted; only Orb Distance required UI. |
| 09:53:19 | The exact owned ordinary launch advanced the carrier to `launch_dispatched` with `result=accepted`. |
| 09:53:25 | The first stable RUNNING boundary accepted the same carrier with `continuity_verified=True` and `state=bound_running`. Damage Slider then consumed its exact carried fact. |
| 09:53:33–09:53:52 | Orb Distance used its declared UI fallback, found Range 98.38m with Extra 87.16m and Workshop 80.37m already matching, and changed nothing. |
| 09:53:59 | Session preflight consumed the carried Bots, Cards, Guardians, Workshop, and Modules facts. |
| 09:54:02 | Session preflight consumed Ultimate Weapon primaries, Poison Swamp Stun, and Spotlight Missiles, then completed successfully with the configured observation-only Module variations. Its Module and Ultimate Weapon sources were `bound_player_save_preflight`. |
| 09:53:19–09:54:02 | The complete in-battle input inventory contained only the expected Orb Distance menu route. It contained no Cards, Modules, Event/Bots, Guild/Guardians, Workshop, or Ultimate Weapon navigation. |
| 09:54:09–09:54:24 | The same owned receipt opened Exit Battle, dispatched exactly one Surrender, proved Game Over, tapped Home once, and persisted a ready validation result. |
| 09:54:28 | Fresh OCR proved Home `NEW_BATTLE`; the prior session advisory cleared and validation reported complete. |
| 09:55:02–09:55:09 | The runtime acknowledged the exact Pause request, denied a raced Home ad-gem input at its final authority guard, and published fresh `HOME_SCREEN/PAUSED` state. |

## Confirmation boundary

This run proves the deployed carrier transition, corrected Assist-assignment
mapping, exclusive-validation typed authority, same-battle cleanup, and final
Pause guard on their ordinary success path. It does not reproduce the
historical unexplained later-`RUNNING` transition; the regression remains the
authority for that fail-closed successor path.
