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
strategy, handler, recovery, and terminal action. An agent that changes a
Running runtime to Pause or Stop owns restoration of that prior Running posture
as part of completing the work, subject to the exact-owner safeguards in
[`live_action_authority.md`](../live_action_authority.md#cleanup-and-reporting).

Pause, Stop, Take Manual Control, input-owner acquisition, and terminal-policy
writes share the runtime's final cross-process input-dispatch boundary. The
native client cancels an older status read and transmits a control write
immediately. If a request races an input that has already crossed its final
guard, that one atomic ADB command may finish; a lifecycle transaction that has
already changed the source may also perform only the restoration needed to
leave the game in a proved state. The control write cannot be delayed by
passive prechecks, and after it is durably accepted no later compound step or
new automated input may begin. A missing control file, or legacy `RUNNING`
state without a valid request identity, initializes as `PAUSED` rather than
granting implicit input authority.

State and terminal-policy acknowledgement is request-ID exact. A repeated
same-value request remains pending without changing its ID until that exact
request is applied; an already acknowledged or stopped-policy repeat is a
no-op.

`automation_ctl.py` changes authority/workflow directives; it does not manage
the systemd process lifecycle. Use the control surface's separate **Start
Automation**/**Stop Automation** process actions for that lifecycle. Start
Automation launches Paused. It waits for explicit battle intent unless the
preceding complete Stop retained fresh proof of an exact battle automation
owned. In that case Start creates a fresh Attach, enables only for its guarded
validation, and completes only after a forced save proves the same battle.
Different or ended battle evidence leaves Automation Paused.

Start Battle is accepted only with fresh verified Home `NEW_BATTLE` evidence;
Attach to Battle requires fresh Home `RESUME_BATTLE` or active-battle evidence.
Unavailable, stale, and mismatched requests fail without substituting the
other route. Start first forces an inactive Home proof, then remains
input-blocked after its tap until the first stable Running save binds the
successor `ActiveRoundIdentity`. Active Attach remains input-blocked at
`validating_save` until one guarded exact-target serialization proves source
restoration and binds a valid identity. Home Resume Attach forces before the
tap and again on the first stable Running frame. There is no Battle History or
log-scope fallback for battle identity. After identity succeeds, unsupported configuration/report
projections may still use their supported UI monitoring. Attach freezes the
accepted selected Strategy definition with the request. No Strategy becomes an
intentional observer; a proven
kind/tier-compatible Strategy becomes active; and an incompatible or
unprovable selection becomes a degraded observer while the selection remains
pending for the next safe boundary. Attached checks never repair the current
battle. Recoverable check, data, and reporting failures complete degraded and
release automation. Only owner, target, canonical identity, authority,
restoration, or uncertain-input loss is catastrophic and leaves input Paused.

Take Manual Control first requests an indefinite Pause and becomes active only
after runtime acknowledgement. Return Control remains Paused; explicit Enable
starts an exclusive reconciliation hold. A newly forced save, or the exact
bound natural Game Over save, is required for identity. After identity is
established, unusable configuration/report projection may use the supported
active/Home/terminal UI discovery route and an identity-bound UI receipt.
A trusted mapped mismatch completes Return with exact degraded evidence; at
Home it is repaired immediately when possible, and exhausted repair still
releases automation. Cached evidence cannot satisfy Return, and an unsafe
source or authority boundary cannot authorize UI input.

`NEXT_BATTLE`, `WAIT`, and `HOME` are terminal dispositions. `NEXT_BATTLE`
normally uses the next authorized Retry/Battle/Resume path after terminal
capture. When a strategy battle ends with repairable configuration degradation,
`NEXT_BATTLE` first goes Home, rearms the next profile's normal setup, and runs
that bounded repair before its exact one-shot launch. Failed Home navigation
stays pending; exhausted repair records the new failure and still launches
degraded. `WAIT` holds Game Over or Home; `HOME` goes Home and suppresses
automatic Battle/Resume input. Pause blocks terminal navigation and Stop exits
without a terminal tap. Game Over statistics and record enrichment are best
effort; the selected terminal route is still attempted. A failed route tap
stays pending for a fresh-evidence retry without changing action authority or
the selected policy. Tournament Results satisfies `WAIT` by retaining the
screen; `NEXT_BATTLE` and `HOME` persist the result and retry the verified
dismissal route under the same authority-preserving rule. Legacy `RETRY`
normalizes to `NEXT_BATTLE`.

A persistent game-speed target is independent of Pause. Acknowledged values
`x0.0`–`x6.0` are exact; `max`/`x6.3` means maximum available. It persists
across battles and process starts until changed.

## Runtime failure policy

Configuration mismatch, unavailable validation/evidence, exhausted repair,
reporting failure, and expired workflow evidence are recoverable. Repair them
only at an already-safe boundary; otherwise flag the exact problem and continue
degraded. They cannot create a global Pause, Stop, Strategy Action Gate, or
indefinite authority hold. For running configuration degradation, a Game Over
handled under already-selected Continue is the next repair boundary: go Home,
run ordinary profile setup, and continue whether that setup succeeds or
exhausts. Legacy session-preflight gates are cleared when the runtime encounters
them.

Automatic Pause is reserved for catastrophic safety failures: lost/corrupt
control authority, lost exact-target ownership, failure to prove source
restoration after lifecycle input, or a dispatched input whose result is
uncertain. Explicit operator Pause, Stop, and Take Manual Control are separate
intent. See
[`architecture/runtime.md`](../architecture/runtime.md#global-runtime-failure-policy).

Low-level ADB mutations and screenshots have bounded subprocess timeouts.
Lifecycle owners such as forced save and watchdog recovery retain typed
attempt/uncertainty evidence across their complete transaction: a timed-out
intermediate command is catastrophic only if the required final source cannot
be freshly proved restored. Recoverable status/report persistence remains
diagnostic and cannot convert a degraded continuation into a shutdown.

## Process replacement and terminal recovery

Before replacing a process, verify its current screen and let an in-flight
guarded action reach a safe boundary. Use the explicit
[Stop Automation then Start Automation](managed_runtime.md#start-or-stop-automation)
path rather than raw `systemctl`: Stop persists `STOPPED`, and Start launches a
new process `PAUSED` with no battle workflow selected. After fresh evidence
proves the distinct PID, exact target, held lock, and observed screen, issue a
separate matching Start Battle or Attach to Battle intent and enable actions
only when appropriate. Replacement never restores the previous action
authority or battle intent implicitly. Never kill a PID solely from possibly
stale metadata.

To recover a preserved terminal after uncertain process ownership:

1. Inspect the screen, owner, lock, exact target, and currently selected
   terminal policy (`WAIT`, `HOME`, or `NEXT_BATTLE`).
2. Stop the known owner cleanly and start the replacement `PAUSED`.
3. Require the new PID/target plus fresh `GAME_OVER/PAUSED` or
   `TOURNAMENT_RESULTS/PAUSED` evidence.
4. Reconfirm the intended terminal policy, then restore Automation Enabled.
   Game Over may collect the fresh terminal and follow Wait, Home, or Continue;
   Continue arms the Retry as a new battle. Tournament Results retains its
   separately documented terminal route.

A terminal-only replacement may preserve Game Stats, Perks, and More Stats,
but it cannot attach process-local Strategy, configuration, timeline, or
sampling evidence without a matching canonical active-round binding. Its fresh
screen route is an explicitly unbound fallback, not proof that the completed
battle was the retained pre-restart battle. Do not leave the terminal manually,
run another battle, and enable an older waiting handler.
