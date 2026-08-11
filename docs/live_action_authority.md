# Live-Action Authority

Read this file after [`live_preflight.md`](live_preflight.md) and before any
device input or battle-boundary action. Task scope permits an operation only
when the current evidence and the exact workflow both grant it; uncertainty
fails closed.

## Universal prohibitions

- Never Surrender a pre-existing or operator-owned battle to create a test
  boundary. Preserve a natural Game Over and pause or stop safely when needed.
- Use fresh source-state evidence immediately before every tap. A transition
  frame, stale screenshot, incomplete capture, old handoff, or target/owner
  mismatch grants no authority.
- Pause blocks every strategy, handler, recovery, and terminal action. Manual
  activity or unexpected navigation requires yielding, not racing the player.
- Exit Battle → Go Home is allowed only when the task authorizes it and the
  active run remains resumable. A safe in-battle setting transition does not
  imply Home, Surrender, restart, or cleanup authority.

## Owned test battles

A battle deliberately started for a bounded test may be Surrendered only when
the task author explicitly authorized that possibility and ownership was
recorded before the verified start. Authority stays with that same runtime,
session, exact ADB target, and battle. Restart, owner or target change, a
resumed/pre-existing battle, Tournament identity, stale evidence, or ambiguous
transition cancels it without cleanup input.

The sole validation-only runtime exception is one ordinary `NEW_BATTLE`
claimed atomically by a profile-declared exclusive-validation receipt before
the verified Home tap. Only the same live owner may use its verified menu,
Exit, Surrender, Game Over, and return-to-Home sequence, and only while fresh
evidence excludes Tournament identity. A later unrelated `RUNNING` battle,
terminal-persistence failure, or ownership ambiguity closes the receipt and
performs no Retry or Surrender.

## Configuration repair

A recoverable configuration mismatch never grants Surrender, Exit Battle,
Go Home, restart, Pause, or another manufactured-boundary authority. Repair is
allowed only when the current boundary already makes that setting transition
safe, such as verified Home `NEW_BATTLE`; otherwise retain the exact degraded
evidence and continue. Exhausted or unavailable repair releases its bounded
owner. This does not alter the separately owned validation-battle exception
above.

Poison Swamp Stun, Damage Slider, Orb Distance, and other supported in-battle
changes use their typed, freshly verified safe-transition contracts in
[`architecture/runtime.md`](architecture/runtime.md#matching-and-action-authority).
Those contracts authorize only their bounded setting action.

## Cleanup and reporting

An agent-owned work Pause must be reconciled when the task ends. Reinspect
control, owner, target, and screen; restore `RUNNING` only when the Pause is
still agent-owned and no new stop condition exists. Never leave an owned Pause
as undocumented handoff state.

`--fast-game-over` suppresses capture only for a known already-recorded
terminal screen. Restart normally afterward so future battles remain
capture-enabled.
