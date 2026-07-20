# Control Surface Architecture

The primary control surface is a native Windows WPF application. A small
Linux-side service exposes the same repository-local controls and records used
by the automation and CLI. The earlier browser client remains a useful fallback
served by that same API.

```text
Native Windows app
      │ starts/stops passwordless Windows OpenSSH
      │ local-forward (recommended)
      ▼
Linux loopback HTTP server
      ├── write ──► logs/automation_ctl.json ──► automation supervisor
      ├── write ──► ~/.config/thetower/automation-adb.env ──► next-start config
      ├── manage ► fixed thetower-automation.service
      ├── read  ──► logs/actions.log
      ├── read  ──► logs/automation-*.lock
      ├── read  ──► logs/battles/Battle*.json
      └── read  ──► logs/tournaments/Tournament*.json
```

The Windows app is a thin client. A self-contained `win-x64` publish needs no
Python, ADB, repository checkout, browser, or preinstalled .NET runtime on the
operator PC. The Linux adapter remains independently testable and transport
agnostic.

## Authority boundaries

- The persistent control file remains authoritative operator intent. The GUI
  does not maintain a second state store.
- `PAUSED` blocks automation actions but continues capture, detection, lifecycle
  observation, and status reporting.
- The GUI distinguishes a saved directive from runtime acknowledgement. It
  never presents a control-file write alone as proof that the runtime applied
  it.
- Runtime health requires both current lock/PID evidence and a fresh status
  heartbeat. A lock file by itself may be stale.
- Completed-battle JSON is the authoritative statistics source. List responses
  are compact summaries; the full record is loaded only when selected.
- Battle type is evidence-based. A Tournament Results terminal identifies a
  Tournament; standard Game Over plus the shared Tournament/Milestone profile
  identifies a Milestone; Farm strategy/profile identity identifies Farm.
  Tournament settings alone never decide between Tournament and Milestone.
- The allowlisted write surface is pause, timed pause, resume, mode,
  stopped-only strategy configuration, stopped or acknowledged-paused ADB-port
  configuration, and fixed managed-service start/stop.
  There is no arbitrary tap, shell command, process kill, Surrender, file-path,
  or ADB endpoint.
- Complete stop persists `STOPPED` before asking the fixed systemd user service
  to stop. Start always crosses the service boundary under `PAUSED`; a requested
  `RUNNING` directive is saved only after systemd reports the unit active.
- A stopped start request may choose `immediate` startup gates or `next_run`.
  `next_run` attaches to the first already-active/resumable battle: normal
  strategy and handler work continues, but rules explicitly tagged as run
  initialization or session preflight remain suppressed. A terminal result or
  verified Home `NEW_BATTLE` boundary removes the suppression, so the next
  battle runs its real gates without fabricated completion state.
- The API never accepts a PID, executable, service name, or command from the
  Windows client. The Linux server is configured with one validated unit name.
- A malformed control file is reported and preserved rather than overwritten.

Control writers use a companion advisory lock and atomic replacement. Timed
pause expiry revalidates its exact deadline while holding that writer lock, so
an operator extension or replacement with an indefinite pause wins over a stale
expiry attempt.

## Transport and access

The server binds to `127.0.0.1:8787` by default. The Windows app can launch and
own the passwordless OpenSSH tunnel itself. It invokes `ssh.exe` without a
shell, uses only validated destination/port fields, enables BatchMode and
forward-failure detection, and closes its tunnel process when the app exits.
The equivalent manual command is:

```powershell
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
```

SSH provides host authentication and encryption while the HTTP listener remains
unreachable from the LAN. Host-key trust must already exist in the Windows
user's OpenSSH `known_hosts`; passwordless public-key authentication does not
remove that check.

A non-loopback bind is rejected unless the environment variable named by
`--token-env` contains a bearer token of at least 24 characters. The built-in
server is plain HTTP, so a direct LAN bind should be used only behind a TLS
reverse proxy or on an otherwise protected network. The same-origin GUI keeps
the token in browser session storage. The native client keeps its token in
memory only. The API deliberately sends no CORS permission.

## Version 1 API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/status` | Control intent, acknowledgement, latest observation, and runtime evidence |
| `POST` | `/api/v1/control` | Allowlisted control mutation |
| `POST` | `/api/v1/process` | Start/stop the fixed systemd automation unit, select its startup-gate policy, configure its stopped strategy, or configure/safely hand off its ADB port |
| `GET` | `/api/v1/battles?limit=N` | Newest Battle and Tournament summaries |
| `GET` | `/api/v1/battles/{battle_id}` | One full structured battle record |
| `GET` | `/api/v1/activity?limit=N&levels=ERROR,WARN` | Recent structured action-log entries, optionally filtered by level |

Control request examples:

```json
{"action": "pause"}
{"action": "pause", "minutes": 30}
{"action": "resume"}
{"action": "mode", "mode": "WAIT"}
```

Process request examples:

```json
{"action": "start", "run_state": "PAUSED"}
{"action": "start", "run_state": "RUNNING"}
{"action": "start", "run_state": "RUNNING", "startup_gate_policy": "next_run"}
{"action": "stop"}
{"action": "set_adb_port", "adb_port": 5565}
{"action": "set_strategy", "strategy": "tournament"}
```

## Current GUI capabilities

- Persistent indefinite and timed pause, including replacing or extending an
  existing timed pause.
- Resume and Game Over mode selection (`RETRY`, `WAIT`, or `HOME`). State and
  mode controls highlight the saved selection; amber means a live runtime has
  not yet acknowledged the latest directive.
- Complete automation-service start (paused or running) and stop through a
  fixed systemd user unit.
- Optional attachment to an existing battle on process start. The selected
  policy persists on Linux; attached-run startup/session gates wait for the
  next real run boundary while ordinary automation remains available.
- Persistent ADB-port selection for the next managed start, plus live handoff
  while the runtime has acknowledged `PAUSED`. The API accepts only an integer
  TCP port; the runtime keeps Pause and its former target if new-target
  connection or screenshot validation fails.
- Validated next-start strategy selection (`farm_t18`,
  `farm_t19_experiment`, `tournament`, or `none`) while automation is stopped.
- Native control of a passwordless Windows OpenSSH tunnel.
- Separate operator-directive and observed-UI state, with acknowledgement and
  stale-heartbeat indicators.
- Current wave, coins/minute, menu, secondary states, and overlays from the
  latest status report.
- Target, owner PID, lock state, and runtime-start evidence.
- A live process PID in the top banner plus systemd MainPID/runtime-lock PID
  comparison in the detailed evidence view; stale lock PIDs are never promoted
  as live process identity.
- Most-recent completed-battle summary in the operational window, with unified
  completed-run history in a separate native window. The history includes
  Farm/Tournament/Milestone classification, strategy, tier, wave, duration,
  Coins/hour, Cells/hour, capture quality, full sections, captured perks,
  resolved settings, and preflight evidence.
- Local filters for type, Tier, minimum/maximum wave, strategy, and quality.
- Draggable layout dividers across the operational control sections and between
  the history list and selected-battle report.
- Independently refreshed recent activity with newest-entry following and
  server-side level/preset filters, without granting general log-file access.
- A responsive browser fallback served by the Linux adapter.

## Deliberately deferred capabilities

These are the next useful additions, in approximate priority order:

1. Publish a small atomic runtime-status JSON snapshot directly from the
   automation. This should include an observation sequence/time, current UI
   state, battle identity, wave, strategy/profile, action gate, active handler,
   and last error. It will replace action-log parsing as the primary live view.
2. Add recovery-timer controls such as extend, cancel, and return-now only after
   those operations have explicit runtime directives and freshness/authority
   checks. The GUI must not implement them as direct taps.
3. Detect likely manual-player activity, automatically yield tap authority, and
   show the grace-period countdown and ownership in the GUI.
4. Add an optional current screenshot with capture time and a prominent stale
   watermark. It should remain read-only and rate-limited.
5. Add battle comparisons, trend charts, CSV export, and aggregate rates by
   strategy, tier, profile, battle type, and date range.
6. Add opt-in notifications for battle completion, invalid capture quality,
   stale runtime, control acknowledgement timeout, and blocked preflight.
7. Extend next-start strategy selection to safe custom YAML plans. Such changes
   should show a resolved configuration diff before they take effect.
8. Support multiple ADB targets with independent authority, history, and health
   views.
9. If access expands beyond an SSH tunnel and one trusted operator, add TLS,
   named users/roles, request IDs, and a durable control audit log before adding
   more write operations.
