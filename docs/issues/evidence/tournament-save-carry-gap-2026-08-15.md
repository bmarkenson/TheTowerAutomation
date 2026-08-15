# Tournament validation save-carry gap — 2026-08-15

This narrow dated extract supports `ISSUE-2026-050`. It records historical
production evidence from the operator-authorized one-shot Tournament
validation; it is not a statement about current runtime state.

## Source and extraction

- Source: production `logs/actions.log` for runtime
  `e958646665f74f2c925220f7414d9408`, exact target generation 1 on
  `localhost:5555`.
- Window: 2026-08-15 08:48:27 through 08:53:44 PDT.
- Read-only extraction: `sed -n '113600,113775p' logs/actions.log`, followed by
  selection of the rows below. Request IDs, raw save data, decoded objects,
  account fields, Module identifiers, and the detailed assignment values were
  intentionally omitted.

## Retained rows

| PDT | Retained evidence |
| --- | --- |
| 08:48:27 | Save-first Home preflight completed with a trusted version-1101 snapshot. Accepted checks included Cards, Card recharge modes, Workshop, Bots, Guardians, Modules, Damage Slider, Poison Swamp Stun, Ultimate Weapon primaries, and Spotlight Missiles. |
| 08:48:28 | Modules reported `source=player_save_preflight`, `complete=True`, `supported=True`, and `disposition=save_observation`; all eight assignments were available, with three matching the observation-only Tournament profile. |
| 08:48:31 | The verified ordinary `NEW_BATTLE` input was dispatched after the durable exclusive-validation claim. |
| 08:48:38 | First RUNNING carry binding reported `pending_or_rejected`, `battle_started=True`, `stable_running=True`, `state=invalidated`, and `reason=first_running_boundary_continuity_failed`. No preceding carried-launch-binding row existed for this launch. |
| 08:51:43 | The session navigator opened Cards. |
| 08:51:57 | The session navigator opened Modules. |
| 08:52:13–08:52:25 | The session navigator opened Event and its Bots tab, then returned to battle. |
| 08:52:33–08:52:41 | The session navigator opened Guild and its Guardian tab, then returned to battle. |
| 08:52:52–08:53:16 | The session navigator used the guarded Exit Battle → Go Home route to inspect Workshop, then resumed the same owned battle. |
| 08:53:23 | Session preflight completed without the former mapping-callback exception. Its only blocking mismatch was the separately observed Poison Swamp Stun state. |
| 08:53:32–08:53:44 | The owned battle received one guarded Surrender, reached Game Over, returned Home, and persisted a ready validation result. |

## Repository correlation

The ordinary Home-launch path already called
`PlayerSavePreflightCoordinator.mark_runtime_launch()` after a conclusive
authorized dispatch. The direct exclusive-validation launcher dispatched its
owned New Battle and persisted its workflow/validation ownership, but did not
perform that save-carry transition. `CarriedPlayerSaveEvidence.bind_running()`
therefore rejected the first battle frame by design: a carrier still in
`pending_launch` cannot be associated with a battle merely because RUNNING was
observed. The UI traversal was the safe fallback after that rejection, not a
decoder or Assist-assignment failure.
