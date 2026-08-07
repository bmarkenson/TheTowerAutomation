# Process and Control Operations

Complete [`live_preflight.md`](../live_preflight.md) before using these controls
against production. Commands below assume the applicable checkout and control
file have been deliberately selected.

## Pause, mode, and game speed

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/automation_ctl.py pause --minutes 15
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py resume
.venv/bin/python tools/automation_ctl.py mode wait
.venv/bin/python tools/automation_ctl.py game-speed 4.0
.venv/bin/python tools/automation_ctl.py game-speed max
```

Wait for the current runtime to acknowledge a directive. `PAUSED` still permits
capture, detection, lifecycle observation, and status, but blocks every
strategy, handler, recovery, and terminal action. An agent-owned work Pause
must be reconciled under
[`live_action_authority.md`](../live_action_authority.md#cleanup-and-reporting).

`NEXT_BATTLE`, `WAIT`, and `HOME` are terminal dispositions. `NEXT_BATTLE`
uses the next authorized Retry/Battle/Resume path after terminal capture;
`WAIT` holds Game Over or Home; `HOME` goes Home and suppresses automatic
Battle/Resume input. Pause blocks terminal navigation and Stop exits without a
terminal tap. Legacy `RETRY` normalizes to `NEXT_BATTLE`.

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
4. Reconfirm `WAIT`, then Resume only to let terminal capture proceed.

A terminal-only replacement may preserve Game Stats, Perks, and More Stats,
but it cannot attach process-local Strategy, configuration, timeline, or
sampling evidence without same-process active-battle continuity. Do not leave
the terminal manually, run another battle, and Resume an older waiting handler.
