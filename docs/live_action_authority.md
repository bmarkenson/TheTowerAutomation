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
terminal-persistence failure, or ownership ambiguity grants no further device
input. Failed result persistence retains the exact receipt and typed owner so a
later heartbeat can retry only the write. If a later unrelated `RUNNING` frame
follows proven Game Over before verified Home cleanup, the runtime persists a
failed old result and releases it before that successor can be adopted; it
performs no Retry, Home, or Surrender input.

Fresh owned-start and natural-terminal evidence is retained before activity
continuity or a fallible receipt write can consume its only observation frame.
Pause denies every write and input while preserving that exact proof for
Resume. A transient claimed-start or confirmed-launch result-write failure is
retried as receipt-only work; it never grants continuity, a second launch, or
Surrender against a later screen. Confirmed-launch prompts and manual-start
observation remain bound to the ADB target on which validation completed.
If Surrender never proves Game Over, durable failure releases the receipt but
leaves a separate suppressive battle hold. No strategy, handler, workflow,
continuity, background, target-handoff, or Strategy-replacement input is
allowed until fresh Game Over, Tournament Results, Workshop, Tournament entry,
or verified Home `NEW_BATTLE` proves the real boundary; resumable Home,
continued Running, unknown state, and incomplete Home-control evidence do not
release it. A dispatched confirmed Tournament launch follows the same rule if
its receipt times out or is superseded before fresh start proof. Its verified
OPEN/BATTLE helpers preserve typed dispatch uncertainty: an uncertain input
Pauses, retains suppressive ownership, and is never replayed as a proven miss.

A failed fresh receipt-ownership reread is uncertainty, not release. The
runtime retains its cached exact validation or confirmed-launch identity as a
suppressive hold, blocks ordinary input and ADB target handoff, and waits for a
fresh exact-owner read or a durable orphan transition. Setup Capture and new
interactive-development lease admission cannot displace that boundary.

A Free Ticket blocker grants no independent authority. It may Claim once only
under the exact typed source that dispatched the obscured launch. Explicit
Start and a linked validation receipt share that one physical budget; a
transient result-write failure may retry persistence but cannot switch aliases
or dispatch Claim again. A newly durable maintenance or confirmed-launch owner
that appears before the next final mutation guard blocks the older route even
before its normal heartbeat hold is installed.

An explicitly authorized interactive-development test may instead preclaim one
ordinary battle with `owned_battle_start=true` while a fresh exact Home
`NEW_BATTLE` lease is active. The Home preclaim is provisional; activity scope
is irrelevant. Guarded terminal cleanup requires the same runtime/PID, exact
target and generation, and a force-bound non-Tournament `ActiveRoundIdentity`
matching Game Over. If the suppressive lease prevents that identity checkpoint,
production declines cleanup. The lease itself ends at Game Over; a proven
process-local claim may authorize only the minimal return-to-Home route. It does not authorize Retry, terminal
lease reacquisition, representative collection, another battle, or retroactive
ownership. Pause, Stop, replacement, or ambiguity sends no cleanup input.

## Configuration repair

A recoverable configuration mismatch never grants Surrender, Exit Battle,
Go Home, restart, Pause, or another manufactured-boundary authority. Repair is
allowed only when the current boundary already makes that setting transition
safe, such as verified Home `NEW_BATTLE`; otherwise retain the exact degraded
evidence and continue. Exhausted or unavailable repair releases its bounded
owner. This does not alter the separately owned validation-battle exception
above.

Attach is always a read-only configuration boundary. Even a setting that is
normally safe to change in battle is measured without mutation while Attach is
adopting an existing battle. The selected compatible Strategy may continue its
ordinary non-attachment behavior after adoption, but the attachment pass
itself grants no repair authority.

Poison Swamp Stun, Damage Slider, Orb Distance, and other supported in-battle
changes use their typed, freshly verified safe-transition contracts in
[`architecture/runtime.md`](architecture/runtime.md#matching-and-action-authority).
Those contracts authorize only their bounded setting action.

## Cleanup and reporting

Before an agent changes a live `RUNNING` runtime to `PAUSED` or `STOPPED` for
its work, it must retain the prior control request and owner. The work is not
complete merely because its code, diagnosis, validation, or deployment is
finished. Reinspect the current/replacement runtime, exact control request,
owner, target, and screen, then restore `RUNNING` when the changed boundary is
still agent-owned and no newer operator, manual-control, safety, target, or
screen condition forbids restoration. A process replacement that starts
Paused is part of the same restoration obligation when the agent stopped a
previously Running owner.

Do not rebase that obligation onto an agent-created Pause merely because the
agent requested it immediately before Stop, process replacement, validation,
or another nested part of the same work. That Pause remains agent-owned across
the boundary. Conversely, an operator Pause that existed before the work or a
newer operator Pause supersedes the earlier Running posture and must remain
Paused. Never restore a Pause whose request or owner was superseded, or a
safety/manual-control boundary. If fresh evidence makes restoration unsafe or
impossible, leave the state unchanged and report the exact blocked handoff; do
not silently present the agent-owned work as complete.

`--fast-game-over` suppresses capture only for a known already-recorded
terminal screen. Restart normally afterward so future battles remain
capture-enabled.
