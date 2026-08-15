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
That composite value must prove `RUNNING` control, an unexpired 120-second
heartbeat, matching request/runtime/session/PID/target/lock acknowledgement,
and production's installed `external_development` hold. Capture, detection,
and status continue; production input, recovery, initialization, validation,
strategy, handler, and lifecycle action are suppressed.

### Preclaim one owned development battle

When the task explicitly authorizes starting—and, if needed, Surrendering—one
bounded test battle, request the owned variant before the verified Battle tap:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"operation":"request","owner_label":"bounded owned test","owned_battle_start":true}' \
  http://127.0.0.1:8787/api/v1/interactive-development-lease
```

Require capability `interactive_development_owned_battle_v1` and the ordinary
composite `active: true` acknowledgement. The request is accepted only from a
fresh exact Home `NEW_BATTLE` observation with force-proven inactive state and
a positive target generation. The preclaim is provisional; log scope is not
battle identity.

At Game Over the input lease terminalizes. The exact process-local claim may
authorize the minimal return-to-Home handler only when a forced
`ActiveRoundIdentity` was established for the owned run and matches the
terminal. The suppressive lease normally prevents that runtime lifecycle
checkpoint, so cleanup is deliberately declined unless the identity was
established through an explicit compatible workflow. It cannot Retry, collect
a representative record, adopt a pre-existing or Tournament battle, or
authorize a new terminal lease. Pause, Stop, runtime/PID/target-generation/
identity replacement, an unexpected terminal, or ambiguous evidence cancels
cleanup input. Do not use this option when the task authorizes observation only.

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

The server-owned 120-second window leaves acknowledgement and guarded
multi-screen work enough time without making the lease indefinite. Each
heartbeat resets that fixed window. The acknowledged expiry must still leave
the selected ADB timeout plus two seconds for timestamp precision and dispatch.
A tap needs at least 7 seconds; a 5000 ms
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
terminates an ordinary lease. The explicitly preclaimed owned-battle variant
may remain active through its exact Home-to-running boundary, then terminalizes
at Game Over while production owns only the minimal Home cleanup described
above. Request a new lease after any terminal boundary and never replay an
uncertain command.
