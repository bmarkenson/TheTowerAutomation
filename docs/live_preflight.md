# Live Preflight

Complete this preflight before process or device interaction, live validation,
diagnosis that depends on current or changing runtime state, or a claim about
current runtime state. Reading closed battle records, historical log ranges,
retained telemetry, or retained fixtures solely as historical evidence does
not require it and does not prove current state. Repository-local edits do not
require it. Repeat the affected checks when the runtime, target, control,
screen, or task scope changes.

## Required reading

1. Read the 218-word
   [`Global live-preflight hazards`](observed_issues.md#global-live-preflight-hazards)
   section. Load a domain issue row or full dossier only when its stated
   condition matches the task or observed symptom.
2. Use [`runtime_operations.md`](runtime_operations.md) to select only the
   operation being performed. Read
   [`live_action_authority.md`](live_action_authority.md) before any input or
   battle-boundary action.
3. Read only the relevant section of
   [`sandbox_boundaries.md`](sandbox_boundaries.md) when interpreting host PID,
   OS-lock, user-systemd, ADB, socket, or execution-wrapper evidence.

## Fresh evidence sequence

Inspect the production checkout and artifacts, even when implementation is in
a feature worktree:

```bash
git -C /home/brianm/dev/python/TheTower status --short
git -C /home/brianm/dev/python/TheTower log -6 --oneline
git -C /home/brianm/dev/python/TheTower for-each-ref \
  --format='%(objectname) %(refname)' refs/thetower/promotion-owner
sed -n '1,160p' /home/brianm/dev/python/TheTower/logs/automation_ctl.json
for lock in /home/brianm/dev/python/TheTower/logs/automation-*.lock; do
  [ -e "$lock" ] || continue
  printf '%s\n' "$lock"
  sed -n '1,160p' "$lock"
done
curl --fail --silent --show-error http://127.0.0.1:8787/api/v1/status \
  | jq '{runtime, process_service, adb_connection, observation, acknowledgements, interactive_development_lease}'
tail -120 /home/brianm/dev/python/TheTower/logs/actions.log
```

An existing `refs/thetower/promotion-owner` identifies an exclusive mutable
promotion transaction. Unless this thread owns that exact transaction, do not
change the production checkout or services, published artifacts, `origin/main`,
or promotion cleanup topology. Read-only inspection and feature work may
continue.

The control file is persistent intent, not liveness. Lock text is owner
metadata, not proof that the kernel lock remains held. Prefer the host-backed
API's PID, OS-lock, systemd `MainPID`, target-registration, acknowledgement,
and observation evidence. If the API is unreachable, that invocation proves
only reachability failure; use the nonblocking lock probe and approved-host
checks in
[`PID-lock and process checks`](sandbox_boundaries.md#pid-lock-and-process-checks).
Never classify a lock as stale from sandbox `ps`, `/proc`, `pgrep`, `kill -0`,
or user-bus failure.

## Exact target and screen

Resolve the ADB target from matching current control, lock, service, and runtime
evidence; do not assume port 5555 or use the first `adb devices` row. When the
task needs device or screen state, run bounded exact-target reads:

```bash
timeout 8s adb -s localhost:PORT get-state
timeout 10s adb -s localhost:PORT exec-out screencap -p \
  > /tmp/thetower_current.png
```

Follow the
[`ADB boundary`](sandbox_boundaries.md#adb-reads-and-connection-management) on
an invocation-environment failure. Do not start, kill, or connect an ADB daemon
as a probe. A `device` result proves transport only. Raw, stale, unsupported-
geometry, majority-black, or incomplete captures grant no action authority;
use a fresh project-validated frame before input.

## Stop conditions and next workflow

Before input, establish the same live owner, exact target, current control and
acknowledgement, complete current screen, recent action history, and the
operation's specific authority. Pause blocks actions. Manual activity,
unexpected navigation, owner/target mismatch, a natural Game Over, or any
ambiguous transition stops the planned input; preserve the boundary and
reassess instead of racing or manufacturing a test state.

Record the evidence time and timezone in any live report. Do not carry these
facts into stable guidance, and do not reuse them as another thread's current
state.
