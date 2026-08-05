# Codex Sandbox and Host Boundaries

This is the canonical guide for interpreting environment-sensitive commands in
TheTower development threads. The workspace sandbox can share repository files
while isolating host PIDs, the user systemd bus, process lifetime, or network
operations. A command failure therefore describes that invocation first; it is
not automatically evidence about the host runtime, service, ADB server,
emulator, or code under test.

Use this guide for process or lock diagnosis, user-service inspection, ADB work,
localhost-socket tests, or a process that must survive an execution call. Use
[`runtime_operations.md`](runtime_operations.md) as the live automation
runbook and complete its safety inspection before device interaction.

## Evidence must stay within its boundary

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| `automation_ctl.py status` or `logs/automation_ctl.json` | Persistent operator intent such as `RUNNING`, `PAUSED`, or `STOPPED` | Whether an automation process is alive or has acknowledged that intent |
| Lock-file `state: held` metadata | The last recorded owner and target did not record a clean release | Whether the OS lock is still held or the owner is alive |
| A nonblocking `flock` conflict | A process still holds the OS lock on that inode | Whether its host PID is visible in the sandbox |
| No match from sandbox `ps`, `pgrep`, `/proc`, or `kill -0` | The PID is not visible in that invocation's process namespace | That the host process exited or the lock is stale |
| `systemctl --user` cannot connect to the bus | That invocation cannot reach the host user-systemd bus | That the unit is inactive, failed, or absent |
| Host-backed API lock/PID evidence plus a matching systemd `MainPID` and fresh observation | A live managed runtime with a current heartbeat | That an older screenshot or handoff still represents the visible game screen |
| Sandbox ADB daemon, socket, or loopback failure | That invocation could not reach or start the required ADB path | That the host ADB server, SSH forward, emulator, or target is down |
| Exact-target host `get-state` returns `device` | The named target is currently connected through the host ADB server | That a raw screenshot is complete or safe input authority |
| A shell command returned success after launching a child | The wrapper accepted the launch command | That the child survived the wrapper or acquired runtime ownership |

Report the exact command, execution boundary, exit status, and error. Use
phrasing such as "not visible from the command sandbox" or "the sandbox could
not reach the user bus" until host-backed evidence supports a stronger claim.

## PID-lock and process checks

The preferred read-only process source is the Linux control-surface API. It
runs in the host environment and reports the OS-lock probe, PID liveness,
systemd service state and `MainPID`, persistent ADB registration, and the latest
runtime observation:

```bash
curl --fail --silent --show-error http://127.0.0.1:8787/api/v1/status \
  | jq '{runtime, process_service, adb_connection, observation, acknowledgements}'
```

This endpoint is host-backed even when the `curl` client runs in a sandbox. A
failed sandbox request means only that the endpoint was not reachable from that
invocation. It does not prove that either Linux service is stopped.

If the API is unavailable, inspect the metadata and the OS lock before asking
the host process table. Use the target named by the control/lock evidence, not a
blind `5555` assumption:

```bash
sed -n '1,160p' logs/automation-localhost_5555.lock
flock -n -E 75 logs/automation-localhost_5555.lock true
```

For the `flock` probe, exit `0` means the OS lock was free and was acquired and
released by the probe; exit `75` means another process still holds it. Any
other exit is a probe error, not a lock-state result. The probe does not rewrite
the metadata.

Use approved host execution when PID or user-systemd confirmation remains
necessary:

```bash
ps -p <lock-pid> -o pid=,ppid=,lstart=,args=
systemctl --user show --no-page \
  --property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus \
  thetower-automation.service
```

Interpret the combined evidence as follows:

- `state: released` with a cleared PID is historical metadata; no stale-owner
  investigation is needed.
- `state: held` with a free OS lock is stale acquisition metadata. Confirm that
  no host owner or managed service remains before starting a replacement.
- `state: held` with a held OS lock is not stale merely because sandbox PID
  tools return no match. Obtain the host-backed API or approved-host process
  evidence and do not unlink the file, kill a guessed PID, or start a competing
  runtime.
- A live PID is not sufficient by itself. For managed automation, correlate the
  systemd `MainPID`, held target lock, startup record, control acknowledgement,
  and fresh observation.

`logs/actions.log` supplies lifecycle and heartbeat evidence, but other
components can append audit entries. Prefer a recent runtime `STATUS` or state
detail associated with the same owner, not file modification time alone.

## ADB reads and connection management

The trusted project permission profile allows loopback reads to the established
host ADB server when that profile is loaded. A bounded exact-target read may
therefore run in the normal sandbox only when the current session declares
working project loopback access:

```bash
timeout 8s adb -s localhost:5555 get-state
timeout 10s adb -s localhost:5555 exec-out screencap -p \
  > /tmp/thetower_current.png
```

Adjust the target from current lock/control/process evidence. Do not infer the
automation target from the first row of `adb devices`, and do not omit `-s`
when more than one target can exist.

`adb connect`, `adb start-server`, and `adb kill-server` are daemon or
connection management, not availability probes. Do not run them from an
isolated sandbox to decide whether ADB is available. For systemd-managed
automation, the persistent control-surface service owns normal exact-target
registration and reconnects; the runtime observes that connection and verifies
fresh frame evidence. A direct manual runtime owns its own reconnect fallback.
When explicit development connection management is actually required by the
task, run it through approved host execution against the exact target.

If a bounded read fails with an invocation-level error such as a smartsocket
permission failure, inability to contact or start the daemon, or denied
loopback access, retry that same read once through approved host execution. Do
not first run `adb connect`, start a competing daemon, change ports, or report
the emulator unavailable. Only the host-path result can promote the diagnosis:

- exact target returns `device`: the earlier failure was environment-only;
- exact target is `offline`, `unauthorized`, or absent: investigate the host
  ADB/tunnel/target path;
- host command itself cannot run: report that narrower host-path blocker.

Raw `adb` captures lack the project's incomplete-frame rejection, geometry
normalization, and action matching. Follow the live-action rules in the
runbook before using a frame for anything beyond read-only inspection.

## User systemd and files outside the workspace

The sandbox can read the shared checkout while lacking the host user bus.
`Failed to connect to bus`, `No data available`, and similar errors are
environment failures. Read service evidence through `/api/v1/status` or rerun
the exact `systemctl --user` inspection through approved host execution.

Normal automation lifecycle changes should use the fixed control-surface
process endpoint because it enforces Pause, owner, attachment, and readiness
boundaries. A raw `systemctl` action does not provide those guards. Installing
units or changing `~/.config/thetower/automation-adb.env` writes outside the
workspace and requires the applicable host authority; do not create a
repository-local substitute to evade that boundary.

## Long-lived processes

The execution wrapper may reap a detached child after a successful `nohup ...
&` launch. Use the fixed systemd automation service for managed runtime work or
a persistent execution session when a development process genuinely must
remain attached. Never treat shell exit `0` as startup completion.

Verify a replacement with all applicable evidence: a distinct host PID,
refreshed held lock, startup log, control consumption, and first runtime
observation. See the preserved
[detached-child operational lesson](issues/resolved-2026.md#a-detached-child-may-not-survive-the-agent-execution-wrapper).

## Localhost sockets in tests

Loopback permission and socket creation are separate from filesystem access.
When a test alone fails because the sandbox denies binding or connecting a
localhost socket, classify the exact error as environmental and rerun only the
affected test through approved host execution. A passing host rerun accounts
for that test; a host-path failure remains a product/test failure to diagnose.
Do not skip it silently, change the test to hide the restriction, or describe
the whole suite as passing without reporting both runs.

Conversely, do not assume every localhost test requires host execution. Run it
normally when the current permission profile supplies the capability and use
the fallback only after a real environment-level failure.

## Before making a runtime claim

Keep these layers separate in the report:

1. persistent directive;
2. lock metadata;
3. kernel OS-lock state;
4. host-backed PID and systemd evidence;
5. fresh runtime heartbeat;
6. exact ADB target state; and
7. fresh visible screen when UI state matters.

Do not call a lock stale, automation stopped, ADB down, or a test broken by
collapsing a failure from one layer into another.
