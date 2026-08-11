# Process and Control Operations

Complete [`live_preflight.md`](../live_preflight.md) before using these controls
against production. Commands below assume the applicable checkout and control
file have been deliberately selected.

## Action authority, battle workflow, and terminal policy

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/automation_ctl.py pause --minutes 15
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py enable
.venv/bin/python tools/automation_ctl.py start-battle
.venv/bin/python tools/automation_ctl.py attach-battle
.venv/bin/python tools/automation_ctl.py take-manual-control
.venv/bin/python tools/automation_ctl.py return-control
.venv/bin/python tools/automation_ctl.py when-battle-ends wait
.venv/bin/python tools/automation_ctl.py game-speed 4.0
.venv/bin/python tools/automation_ctl.py game-speed max
```

Wait for the current runtime to acknowledge a directive. `PAUSED` still permits
capture, detection, lifecycle observation, and status, but blocks every
strategy, handler, recovery, and terminal action. An agent-owned work Pause
must be reconciled under
[`live_action_authority.md`](../live_action_authority.md#cleanup-and-reporting).

State and terminal-policy acknowledgement is request-ID exact. A repeated
same-value request remains pending without changing its ID until that exact
request is applied; an already acknowledged or stopped-policy repeat is a
no-op.

`automation_ctl.py` changes authority/workflow directives; it does not manage
the systemd process lifecycle. Use the control surface's separate **Start
Automation**/**Stop Automation** process actions for that lifecycle. Start
Automation always launches Paused and waits for explicit battle intent.

Start Battle is accepted only with fresh verified Home `NEW_BATTLE` evidence;
Attach to Battle requires fresh Home `RESUME_BATTLE` or active-battle evidence.
Unavailable, stale, and mismatched requests fail without substituting the
other route. Attach remains input-blocked at `validating_save` until one
guarded exact-target serialization proves source restoration and either binds
usable save evidence or selects the established Battle History UI fallback.
An absent, unsupported, incompatible, or unprojectable save after safe
restoration therefore still adopts the battle for observation only and keeps
supported UI monitoring available. Owner, target, scope, authority, or
restoration loss blocks input. Applying a Strategy remains a separate explicit
action.

Take Manual Control first requests an indefinite Pause and becomes active only
after runtime acknowledgement. Return Control remains Paused; explicit Enable
starts an exclusive reconciliation hold. A newly forced save, or the exact
bound natural Game Over save, is preferred. When that save is unusable after a
safe source restoration, the same hold automatically uses the supported
active/Home/terminal UI discovery route and a target/scope-bound UI receipt.
A trusted mapped mismatch completes Return with exact degraded evidence; at
Home it is repaired immediately when possible, and exhausted repair still
releases automation. Cached evidence cannot satisfy Return, and an unsafe
source or authority boundary cannot authorize UI input.

`NEXT_BATTLE`, `WAIT`, and `HOME` are terminal dispositions. `NEXT_BATTLE`
uses the next authorized Retry/Battle/Resume path after terminal capture;
`WAIT` holds Game Over or Home; `HOME` goes Home and suppresses automatic
Battle/Resume input. Pause blocks terminal navigation and Stop exits without a
terminal tap. Game Over statistics and record enrichment are best effort; the
selected terminal route is still attempted. A failed route tap stays pending
for a fresh-evidence retry without changing action authority or the selected
policy. Tournament Results satisfies `WAIT` by retaining the screen;
`NEXT_BATTLE` and `HOME` persist the result and retry the verified dismissal
route under the same authority-preserving rule. Legacy `RETRY` normalizes to
`NEXT_BATTLE`.

A persistent game-speed target is independent of Pause. Acknowledged values
`x0.0`–`x6.0` are exact; `max`/`x6.3` means maximum available. It persists
across battles and process starts until changed.

## Runtime failure policy

Configuration mismatch, unavailable validation/evidence, exhausted repair,
reporting failure, and expired workflow evidence are recoverable. Repair them
only at an already-safe boundary; otherwise flag the exact problem and continue
degraded. They cannot create a global Pause, Stop, Strategy Action Gate, or
indefinite authority hold. Legacy session-preflight gates are cleared when the
runtime encounters them.

Automatic Pause is reserved for catastrophic safety failures: lost/corrupt
control authority, lost exact-target ownership, failure to prove source
restoration after lifecycle input, or a dispatched input whose result is
uncertain. Explicit operator Pause, Stop, and Take Manual Control are separate
intent. See
[`architecture/runtime.md`](../architecture/runtime.md#global-runtime-failure-policy).

## Process replacement and terminal recovery

Before replacing a process, verify its current screen and let an in-flight
guarded action reach a safe boundary. For a managed active-battle replacement,
use the [guarded control-surface reload](managed_runtime.md#reload-automation-for-the-current-battle)
rather than raw `systemctl`. The reload records the prior control intent, then
temporarily persists an indefinite Pause while it stops the known owner and
starts the replacement. This handoff Pause is a safety boundary, not a change
in the operator's intended state. Only after the replacement proves its
distinct host PID, matching held target lock, startup evidence, control
acknowledgement, and first fresh observation does the reload restore the prior
`RUNNING`, indefinite `PAUSED`, or unexpired timed Pause. A timed Pause that
expires during the handoff resolves to `RUNNING`; any failure after Pause
preparation remains `PAUSED`. Never kill a PID solely from possibly stale
metadata.

To recover a preserved terminal after uncertain continuity:

1. Keep mode `WAIT`; inspect the screen, owner, lock, and exact target.
2. Stop the known owner cleanly and start the replacement `PAUSED`.
3. Require the new PID/target plus fresh `GAME_OVER/PAUSED` or
   `TOURNAMENT_RESULTS/PAUSED` evidence.
4. Reconfirm `WAIT`, then select Automation Enabled only to let terminal
   capture proceed.

A terminal-only replacement may preserve Game Stats, Perks, and More Stats,
but it cannot attach process-local Strategy, configuration, timeline, or
sampling evidence without same-process active-battle continuity. Do not leave
the terminal manually, run another battle, and enable an older waiting handler.
