# Interactive Development Lease and Input

Complete [`live_preflight.md`](../live_preflight.md) and read
[`live_action_authority.md`](../live_action_authority.md) before this procedure.
The lease is a cooperative suppressive hold, not Pause, a secret, or general
input authority.

## Request and verify

Request one bounded owner label:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"operation":"request","owner_label":"bounded task label"}' \
  http://127.0.0.1:8787/api/v1/interactive-development-lease
```

A successful write is only a request. Inspect
`interactive_development_lease` in `/api/v1/status` and require `active: true`.
That composite value must prove `RUNNING` control, an unexpired 30-second
heartbeat, matching request/runtime/session/PID/target/lock acknowledgement,
and production's installed `external_development` hold. Capture, detection,
and status continue; production input, recovery, initialization, validation,
strategy, handler, and lifecycle action are suppressed.

Keep the exact lease ID current separately; heartbeats do not enter the action
log:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"operation":"heartbeat","lease_id":"LEASE_ID"}' \
  http://127.0.0.1:8787/api/v1/interactive-development-lease
```

## One exact-target input

While the matching acknowledgement remains active, use only the project helper
with canonical `1080x1920` coordinates:

```bash
.venv/bin/python tools/development_adb_input.py \
  --lease-id LEASE_ID tap 540 960
.venv/bin/python tools/development_adb_input.py \
  --lease-id LEASE_ID swipe 540 1500 540 500 300
```

The helper rechecks the supported API/capability, control, lease lifecycle,
runtime/session, exact target, acknowledgement, expiry, and production-owned
composite `active` value. It acquires native geometry from that target and
revalidates the complete binding immediately before one bounded ADB command.
Tap coordinates use half-open canonical bounds; swipe duration is 1–5000 ms.

The acknowledged expiry must leave the selected ADB timeout plus two seconds
for timestamp precision and dispatch. A tap needs at least 7 seconds; a 5000 ms
swipe needs 9 seconds. If insufficient, heartbeat separately, wait for the
renewed matching acknowledgement, and invoke the helper again. The helper does
not request, heartbeat, extend, revive, release, or retry a lease or uncertain
input.

The default audit is production `logs/actions.log`. If its required `ACTION`
cannot be written, no input is sent. Exit `0` means the one command completed;
`2` is invalid usage, `3` lease/status rejection, `4` geometry/ADB failure, and
`5` audit-write failure. Read-only capture remains under the ordinary ADB
rules; an active lease does not authorize ad-hoc raw input.

## Release

After input has stopped:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"operation":"release","lease_id":"LEASE_ID"}' \
  http://127.0.0.1:8787/api/v1/interactive-development-lease
```

Release completes only after production obtains a fresh post-release frame and
publishes a terminal disposition. Ambiguity keeps the hold visible. Pause or
Stop revokes immediately; Resume does not revive it. Heartbeat expiry,
runtime/session/PID/target replacement, battle boundary, or natural Game Over
terminates it. Request a new lease after any such boundary and never replay an
uncertain command.
