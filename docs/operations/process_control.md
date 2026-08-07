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
other route. Attach currently remains input-blocked at `validating_save` until
the separately owned save-freshness integration is complete.

Take Manual Control first requests an indefinite Pause and becomes active only
after runtime acknowledgement. Return Control remains Paused; explicit Enable
starts an exclusive reconciliation hold. That reconciliation likewise remains
pending in this feature stage and must not be treated as returned authority.

`NEXT_BATTLE`, `WAIT`, and `HOME` are terminal dispositions. `NEXT_BATTLE`
uses the next authorized Retry/Battle/Resume path after terminal capture;
`WAIT` holds Game Over or Home; `HOME` goes Home and suppresses automatic
Battle/Resume input. Pause blocks terminal navigation and Stop exits without a
terminal tap. A Game Over navigation failure Pauses action authority without
rewriting the selected policy. Tournament Results currently satisfies `WAIT`
by retaining the screen; `NEXT_BATTLE` and `HOME` remain visibly pending until
a verified dismissal route exists. Legacy `RETRY` normalizes to `NEXT_BATTLE`.

A persistent game-speed target is independent of Pause. Acknowledged values
`x0.0`–`x6.0` are exact; `max`/`x6.3` means maximum available. It persists
across battles and process starts until changed.

## Strategy Action Gate

An active-battle validation mismatch may raise a Strategy Action Gate while
control remains `RUNNING`. This is not a failed Pause acknowledgement. Capture,
detection, OCR, status, and explicitly allowlisted independent collectors may
continue; strategy and lifecycle actions remain blocked. Resolve it only with
the published retry, run-scoped waiver, explicit Pause/manual change, or
offered guarded repair decision. The gate itself never authorizes Surrender,
Home, restart, or a new battle. See
[`architecture/runtime.md`](../architecture/runtime.md#typed-runtime-action-authority).

## Process replacement and terminal recovery

Before replacing a process, verify its current screen and let an in-flight
guarded action reach a safe boundary. Stop the known owner cleanly, confirm its
exit, and start a replacement under persisted Pause when validation is needed.
A successful launch command is not readiness: require a distinct host PID,
matching held target lock, startup evidence, control acknowledgement, and first
fresh observation. Never kill a PID solely from possibly stale metadata.

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
