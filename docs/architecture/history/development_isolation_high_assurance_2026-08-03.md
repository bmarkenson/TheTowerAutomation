# Superseded High-Assurance Production and Development Isolation Design

> **Historical design, archived 2026-08-04.** This contract assumed that
> processes running as the same trusted Unix user needed adversarial source,
> token, transport, filesystem, and audit protections. The operator clarified
> that TheTower is a single-user hobby project with no malicious-actor or data-
> secrecy concern. The design is preserved as decision history, but it is not
> current policy and must not drive implementation. See the current
> [production and development coordination architecture](../development_isolation.md).
> The remainder is preserved as written, so references to this document as
> “canonical” describe its former status only.

This document is the canonical target architecture for isolated TheTower
development, concurrent feature worktrees, production frame publication, and
coordinated access to the single emulator. Phase 0 of this contract is
implemented; Phases 1–6 remain target architecture and are not available. The
current runtime and control service remain governed by the
[runtime architecture](../runtime.md), the
[control-surface architecture](../control_surface.md), and the
[runtime runbook](../../runtime_operations.md) until the corresponding delivery
phase is complete.

The contract uses **must**, **must not**, **should**, and **may** normatively.
Implementation is tracked in the
[tooling and maintenance backlog](../../backlog/tooling-and-maintenance.md#production-and-development-coordination-delivery).

## Outcomes and invariants

The architecture has four outcomes:

1. production remains continuously identifiable and cannot accidentally run
   code, environments, locks, or mutable state from a development worktree;
2. any number of feature worktrees can perform repository-local work
   concurrently without sharing generated state or silently changing one
   another's dependencies;
3. development normally observes immutable retained fixtures or atomically
   published production frames without touching ADB; and
4. the rare operation that needs the live emulator crosses one host-global,
   production-owned broker with explicit capability, freshness, lease,
   acknowledgement, audit, and cleanup boundaries.

The following invariants apply throughout the migration:

- `/home/brianm/dev/python/TheTower` is the only production checkout. It is on
  `main`, remains the path used by the existing systemd user units, and is the
  only checkout from which a long-lived automation runtime or control service
  may run.
- `/home/brianm/dev/python/TheTower-worktrees/dev` is the `develop` integration
  checkout. Ephemeral feature worktrees live beneath
  `/home/brianm/dev/python/TheTower-worktrees/workers`.
- Production is the sole long-lived runtime, normal ADB connection
  coordinator, and device-input owner. Bounded allowlisted development reads
  granted by the production broker do not transfer that ownership.
- Emulator coordination is host-global. A lock, PID file, control file, or
  virtual environment beneath one checkout can never grant cross-worktree
  emulator authority.
- A development worktree is an experimental source, not a trusted runtime
  installation. Production services must not import it, execute it, add it to
  `PYTHONPATH`, load its virtual environment, or accept a worker-supplied
  executable, module, shell command, service name, or arbitrary filesystem
  path.
- Operator `PAUSED` and `STOPPED` intent outrank every development request and
  lease. Development access never writes or impersonates those states.
- No development lease by itself authorizes Surrender, Exit Battle, Go Home,
  Retry, New Battle, a Tournament transition, process replacement, ADB target
  handoff, or connection management.
- Unknown identity, stale evidence, incompatible revisions, ambiguous UI,
  unexpected navigation, source drift, heartbeat loss, or uncertain cleanup
  fails closed.

## Current implementation boundary

Commit `0a17fef` implements only the Phase-0 Python and generated-state
bootstrap boundary. The implementation has the right production anchor but
does not yet provide any later broker, frame-publication, lease, input, or
promotion capability:

- both checked-in systemd units already execute the fixed production checkout;
- those units run the broker and automation as separate processes; the current
  HTTP service and App loop have no authenticated persistent peer channel or
  App-owned external-command mailbox;
- the control-surface service already owns loopback API policy, capability
  advertisement, systemd/PID/OS-lock evidence, and guarded process operations,
  which is why it is the broker extension point;
- `SingleInstanceLock` derives its default path from its checkout, so two
  worktrees do not share one authoritative coordinator;
- `core/ss_capture.py` captures directly from ADB, while `App` saves the latest
  screenshot relative to its checkout without an immutable atomic bundle or
  reader protocol;
- `RuntimeActionAuthority`, `AuxiliaryRouteLease`, and exclusive-validation
  receipts are process- or profile-scoped production mechanisms, not
  host-global development leases; and
- the Phase-0 environment writer lock serializes dependency publication only;
  it grants no runtime, ADB, emulator, frame, broker, or input authority.

These are migration facts, not permission to approximate the target with a
second runtime, a copied production environment, a worktree-local lock, or raw
ADB input.

## Repository roles and promotion

### Branch and worktree roles

| Role | Canonical branch and path | Allowed work |
| --- | --- | --- |
| Production | `main` at `/home/brianm/dev/python/TheTower` | Installed services, operator state, durable records, broker, frame publisher, and explicitly scheduled production validation |
| Integration | `develop` at `/home/brianm/dev/python/TheTower-worktrees/dev` | Master-owned integration, combined regression, conflict resolution, and promotion candidate preparation |
| Feature worker | Feature branch below `/home/brianm/dev/python/TheTower-worktrees/workers` | One bounded change, repository-local tests, retained-frame analysis, and broker requests under this contract |

“Master” in this document means the coordinating development owner, not a Git
branch. The master serializes integration and every change that affects
authority, lifecycle, broker protocol, capture ownership, production
dependencies, or promotion. Workers may develop independent code,
documentation, fixtures, and tests concurrently, but they do not merge
themselves into `develop` or `main`.

A feature branch starts from an inspected `develop` commit and ends in one or
more coherent worker commits. Workers commit only on their feature branches
and return the exact commit IDs plus validation evidence to the master. They
never commit on `develop` or `main`. A worker must not update the production
checkout, repoint a systemd unit, copy files into production, or use a
production runtime directory as its test root.

Before integration, the master rechecks target-file ownership and the staged
and unstaged state in the `develop` worktree. The master normally
cherry-picks each accepted coherent worker commit into `develop`, reviews the
resulting exact diff, and runs the combined non-live gate. Conflict resolution
is performed and committed only by the master in `develop`; a worker instead
revises its feature commit when the resolution belongs in feature scope.
`main` is never a feature-development or conflict-resolution branch.

Feature branches and worktrees are removed only after their accepted commits
are integrated and every required validation or retained-evidence reference is
durable. Rejection of a feature does not authorize deletion of evidence still
needed to explain an integration or production decision.

### Validation and production promotion

Validation proceeds from least authoritative to most authoritative:

1. pure unit, fake-clock, fake-runtime, and fake-ADB tests in the feature
   worktree;
2. retained fixtures and atomically published frames;
3. combined regression from the `develop` integration checkout;
4. bounded production-coordinated observation or interaction, only when it
   resolves remaining device-integration uncertainty; and
5. master-owned promotion from `develop` to `main` followed by the existing
   guarded deployment and process-replacement procedures when a restart is
   actually required.

An API response, successful broker lease, or live observation does not promote
code. A `main` update does not itself restart automation or confer input
authority. Let `M` be the exact current `main` object and `D` the exact
`develop` commit that passed the combined gate and any separately authorized
validation. Promotion is permitted only when all of the following are true:

- the `develop` branch points at `D` and the master reviewed the complete
  `M..D` commit range and aggregate diff;
- the index and tracked working tree are clean in both the integration and
  production checkouts, neither has an unmerged entry, and every nonignored
  untracked file or modification has resolved ownership and disposition;
- `git status --porcelain=v1 --untracked-files=all` is empty in both
  checkouts immediately before promotion, after their normal ignore rules;
- `git merge-base --is-ancestor M D` succeeds against those exact object IDs,
  and the production `main` branch still resolves to `M`;
- dependency, protocol, migration, rollback, and validation evidence for the
  reviewed range is complete; and
- the current runtime is at a safe deployment boundary under the existing
  guarded deployment procedure.

An existing user-owned or parallel modification in the production checkout
blocks promotion until its ownership and disposition are resolved. It does not
block unrelated feature or integration work in `develop`.

The master promotes only by running the equivalent of
`git merge --ff-only D` in the clean production checkout while it remains on
`main`; the production checkout is never switched to `develop` or a feature
branch. Afterward, the branch and `HEAD` must both resolve to exact object
`D` and the clean-state check must still pass. A non-fast-forward result,
different target object, new local change, or failed post-update check aborts
deployment. Process replacement, when required, remains a separate guarded
operation after the Git fast-forward.

An emergency hotfix committed directly to `main` is the sole exception to the
normal feature route. The master must immediately integrate the updated
`main` history back into `develop`—fast-forward when possible, otherwise by
a master-owned merge and conflict-resolution commit in `develop`—and rerun
the combined gate. No later production promotion is eligible until the
hotfixed `main` commit is again an ancestor of the exact validated `develop`
candidate. Authority- or lifecycle-sensitive changes are promoted in
dependency order and never bundled with an unrelated feature merely to reduce
deployment count.

## Development Python and generated-state isolation

Production's `.venv` belongs only to the production checkout. It must not be
executed, copied, bind-mounted, symlinked, or mutated by `develop` or a worker.
Conversely, systemd must never execute a development environment.

### Phase-0 reproducible bootstrap

Phase 0 is implemented by `0a17fef`:

- `.python-version` declares exact CPython 3.12.3, while `pyproject.toml` is the
  single direct-dependency declaration with runtime, optional player-save,
  developer-tool, and test ownership;
- `requirements/runtime.lock` contains the runtime plus player-save closure,
  `requirements/development.lock` contains the complete development closure,
  and `requirements/bootstrap.lock` pins the resolver/bootstrap toolchain;
  every artifact is exact and SHA-256 checked;
- `requirements/development-environment.json` owns bootstrap schema 2, the
  supported Linux x86_64 platform, configured interpreter, canonical store,
  production exclusion, and fingerprint inputs; and
- `tools/development.py` is the standard-library-only bootstrap, status, lock,
  and checkpoint entrypoint. `tools/development_pytest.py` classifies tests
  that cross into an explicitly excluded host executable without hiding
  repository-local failures.

`requirements-save-import.txt` was deliberately retired. `nrbf` remains owned
by the `player-save` group, is included in both supported environment locks,
and is installed in the complete development environment for its regression
suite. Environment presence alone did not retain `psutil`, `pyautogui`, or the
PyPI `tesseract` package because the source graph does not import them.

Development environments are immutable and content-addressed:

```text
/home/brianm/dev/python/TheTower-worktrees/.environments/
    <python-implementation>-<major.minor>-<dependency-sha256>/
```

The dependency fingerprint covers the interpreter identity, all locked
dependency documents, bootstrap schema, and platform tag. A builder takes the
host-global development-environment writer lock beneath
`$XDG_RUNTIME_DIR/thetower`, creates and validates a sibling staging
environment, syncs it, and atomically renames it to the fingerprinted final
path. Before publication it records an installed-file manifest, removes
ordinary worker write permission, and verifies the manifest through the
checked-in runner. An existing final path is never modified in place; a
writable or mismatched final environment is rejected rather than repaired in
place. Ordinary workers get a worktree-local ignored `.venv` symlink to the
exact final environment and have no supported `pip install` path into it. A
dependency change creates another environment; it cannot mutate workers still
using the prior fingerprint.

Before publication the implementation removes bytecode, rewrites supported
text and console-script staging references, rejects unsupported binary
references, rebuilds installed `RECORD` hashes, scans for the deterministic
staging prefix, verifies the exact installed distribution set, manifests every
remaining directory/file/symlink, removes all write bits, syncs the tree, and
atomically renames the sibling stage. Only then may the worktree `.venv`
symlink be replaced. Reuse revalidates the manifest, permissions, ownership,
platform, relocation contract, module execution, and console script.

The checked-in runner must:

- fail clearly when the expected environment or lock input is absent;
- verify that `sys.prefix` resolves to the development environment, never the
  production checkout;
- use a worktree-owned or `/tmp` bytecode, pytest-cache, coverage, and scratch
  root;
- set no production control, log, screenshot, custom-profile, socket, or ADB
  path; and
- run all project Python through the selected development `.venv/bin/python`.

The implemented checkpoint runs compilation, `test/validate_state_defs.py`,
`test/clickmap_integrity.py --show-orphans`, and the complete pytest
collection. ADB, Tesseract, ffmpeg, and scrcpy are host prerequisites rather
than Python dependencies: the checkpoint reports their path presence without
executing them, blocks their executables, and reports tests that actually
request excluded Tesseract as explicit host-prerequisite skips. It does not
invent a live/configuration validator list; the GC and Tournament validators
remain retained-screen or live workflows outside this ordinary non-live gate.

No Phase-1 source identity, atomic production frame publication, broker
transport, lease, production yield, mediated input, owned validation battle,
or promotion enforcement is implemented by Phase 0.

### Ignored and generated state

Every checkout owns its existing ignored `logs/`, `screenshots/`, `tmp/`,
`out/`, custom strategy data, test caches, and bytecode. Those paths must not
be symlinked between production, integration, and worker checkouts. Tests use
injected temporary roots and may not infer production state from similarly
named files.

The only intentionally shared development state is:

- immutable content-addressed environments;
- the production broker's volatile socket, epoch, lease, and frame data under
  the per-user runtime directory; and
- the broker's durable audit ledger under the per-user state directory.

Canonical regression fixtures remain tracked in `test/fixtures/`. A frame
copied from volatile publication into durable repository evidence must record
its publication metadata and follow the repository's normal fixture and
protected-artifact rules.

## Development source identity

Dirty worktrees are allowed, but a lease must identify their exact source
state. A branch name or HEAD commit alone is insufficient.

### Registration and fingerprint

Before a passive device read, coordinated capture, or exclusive request, the
client registers one canonical worktree path. The broker independently
verifies that the path is a Git worktree belonging to the TheTower repository
and lies beneath the allowlisted integration or worker roots. It never
executes a hook, filter, program, or Python module from that worktree.

Source fingerprint schema 1 is the SHA-256 of canonical JSON containing:

- the broker-configured repository identity and canonical worktree path;
- current branch or explicit detached-HEAD marker;
- HEAD object ID;
- SHA-256 of the staged binary diff;
- SHA-256 of the unstaged binary diff;
- sorted untracked entries with repository-relative path, file type, mode,
  size, and content SHA-256; a symlink hashes its link text, not its target;
- exact submodule object/status entries; and
- fingerprint schema version.

Git inspection disables external diffs, text conversion, paging, prompts, and
hooks. Ignored generated files, `.venv`, and the shared environment root do not
participate. Special files, unreadable paths, worktree escapes, an unstable
index, or a file changing while hashed make registration fail rather than
produce a partial identity. Repository identity is assigned from the
production broker's configured Git common directory and repository ID; a
client-supplied remote URL or repository name cannot substitute for it.

The registration response includes `source_registration_id`,
`development_source_fingerprint`, branch, HEAD, dirty state, canonical path,
creation time, and a default thirty-minute expiry. Refresh is allowed only
after recomputing the same fingerprint. The broker recomputes the full
fingerprint before acknowledging an exclusive request and before every
state-changing input. A heartbeat performs a cheap HEAD/index/manifest change
check and triggers a full recompute on any signal. Inability to prove the
source unchanged is source drift.

Source drift:

- fails a requested or queued operation;
- revokes an acknowledged or active lease before another input;
- prevents renewal and continuation; and
- is recorded with the old and new fingerprints, but never with source file
  contents.

Registering the new state creates a new identity. It does not repair, renew, or
inherit the old request.

## Trust boundary and host-global broker

### Broker ownership decision

The production `thetower-control-surface.service` must be extended as the
host-global development broker. A third daemon is not justified: the existing
service already runs from the production checkout, survives automation-runtime
replacement, owns loopback API policy, verifies systemd/PID/OS-lock evidence,
advertises revisions and capabilities, and has an established audit path.

The extension adds two role-specific per-user Unix-domain listeners without
exposing development authority through the Windows-forwarded TCP listener. It
must not make the HTTP handler or the broker the device-input implementation.
The broker schedules and binds authority; the production runtime acknowledges
yield and its production-owned input gateway performs the final guarded
dispatch.

Each Unix socket is mode `0600` inside a mode `0700` directory:

```text
$XDG_RUNTIME_DIR/thetower/
    development-broker.sock
    runtime-peer.sock
    service-epoch.json
    coordinator/
    clients/
    frames/
```

The service must take one host-global broker/coordinator OS lock in that
directory before advertising readiness. It must not fall back to a repository
path when `XDG_RUNTIME_DIR` is unavailable or unsafe. Read-only production
automation may continue without the development broker, but every development
capability other than retained local fixtures fails unavailable.

This boundary protects against accidental concurrency and stale clients under
the same Unix account. It is not a security boundary against a malicious
process already running as that account. Mode restrictions, opaque tokens,
fixed endpoints, source binding, and audit are still required because they make
authority explicit and failures diagnosable.

### Broker/runtime peer channel

The control-surface broker and automation runtime are separate systemd
processes. Their sole V1 authority channel is a runtime-initiated persistent
`AF_UNIX` stream connection to
`$XDG_RUNTIME_DIR/thetower/runtime-peer.sock`. The broker listens; after the
automation process has acquired its production target lock and created a fresh
runtime session ID, the runtime connects. The runtime never listens for a
worker connection, and there is no file-polling, loopback HTTP, signal, or
second-daemon alternative for yield or input.

The two local roles are deliberately different:

- an **external development client** uses
  `development-broker.sock` and the documented request API. It can register
  source, observe status/frames, and request a capability, but it never speaks
  the runtime-peer protocol; and
- the **internal runtime peer** is the one authenticated
  `thetower-automation.service` MainPID on `runtime-peer.sock`. It publishes
  runtime truth, installs/removes the external hold, and executes accepted
  catalog actions through `App`.

The broker accepts only one registered runtime peer. On every accept it reads
Linux `SO_PEERCRED` before parsing an application message and requires:

- the peer UID to equal the configured production service UID;
- the kernel-reported peer PID to equal both the registration PID and the
  freshly queried systemd `MainPID` for `thetower-automation.service`;
- a fresh opaque `runtime_session_id` created by that process;
- the broker-issued `service_epoch` challenge;
- a production target-lock record naming the same PID and exact ADB target,
  with the expected OS lock still held; and
- agreement between the runtime's target snapshot and the broker-owned
  `adb_target_generation` assigned after that target/lock evidence is
  verified.

The expected lock location/identity comes from production broker
configuration; a path named in registration cannot redirect the check.
Unavailable or ambiguous systemd or OS-lock evidence rejects registration.

The broker repeats the MainPID, session, held-lock, service-epoch, target, and
target-generation checks on registration, every heartbeat/status frame, before
forwarding a yield or input command, and before accepting its result. A target
handoff makes the peer temporarily ineligible: the runtime publishes the new
owned target snapshot, the broker independently verifies the replacement
lock, increments `adb_target_generation`, and acknowledges that generation
before another development operation.

Separate socket paths are necessary but not sufficient authentication. An
external client cannot select `role=runtime` on its API connection, and a
same-user process that opens `runtime-peer.sock` is rejected because its
kernel PID is not the current systemd MainPID and it cannot establish that
MainPID's runtime session plus held target-lock identity. The service-epoch
challenge prevents a pre-restart registration replay. Compromise of the actual
production process or Unix account remains outside this accidental-concurrency
boundary.

#### Framing, registration, and negotiation

The runtime-peer stream uses a four-byte unsigned big-endian length followed by
one UTF-8 JSON object. The maximum encoded object is 64 KiB. A zero/oversized
length, invalid UTF-8/JSON, duplicate key, non-object top level, unknown
field or required enum for the negotiated V1 schema, or frame not completed
within two seconds is a protocol error. The receiver buffers a complete frame
before validation and never acts on a prefix or partial object.

Every frame has `schema_version=1`, `message_type`, `message_id`,
`service_epoch`, and a canonical-payload SHA-256. `broker_hello` carries the
supported protocol range and a null runtime session because negotiation has not
yet occurred; `runtime_register` proposes the runtime's supported range and
fresh session. Every later frame carries the selected
`runtime_peer_protocol`, exact `runtime_session_id`, broker-assigned
`runtime_peer_connection_id` for that accepted socket, and a
`correlation_id` when it belongs to a request or operation. A broker frame
carries the next strictly increasing `broker_sequence` and
`runtime_sequence_ack`; a runtime frame carries the next strictly increasing
`runtime_sequence` and `broker_sequence_ack`. Sequence numbers start at one
for each connection and acknowledge only the highest contiguous peer frame.
They are transport ordering, not the runtime action sequence or a lease
generation.

Normative peer deadlines are integer nanoseconds from Linux
`CLOCK_MONOTONIC`, which is shared by processes on the same boot. The broker
hello includes the host boot ID and current monotonic value; registration
rejects a boot-ID mismatch. RFC 3339 timestamps may accompany messages for
audit readability but never extend a monotonic deadline.

Registration is ordered and must finish within two seconds:

| Order | Message | Required payload |
| --- | --- | --- |
| 1, broker to runtime | `broker_hello` | Service epoch, random connection challenge, host boot ID/current monotonic time, supported protocol min/max, broker capabilities, frame/queue limits, and broker sequence 1 |
| 2, runtime to broker | `runtime_register` | Echoed challenge/epoch, kernel PID as claimed PID, fresh runtime session ID, systemd unit name, supported protocol min/max, compiled runtime capabilities, exact target plus process-local target generation/owned flag, production target-lock identity, runtime action sequence, operator-control revision/state, cleanup/orphan summary, and the complete action-catalog object with revision/digest |
| 3, broker to runtime | `runtime_registered` | Selected protocol, new runtime-peer connection ID, effective capability intersection, verified MainPID/session/lock/target, assigned ADB target generation, service epoch, broker limits, and accepted action-catalog revision/digest |
| 4, runtime to broker | `runtime_status` | First authoritative status/heartbeat under the selected protocol; no yield is eligible before it |

No lowest-common-denominator fallback may omit peer authentication, target
generation, action-catalog binding, sequencing, idempotency, revocation, or
cleanup. With no compatible protocol, the broker sends a bounded
handshake-level `channel_shutdown` with no selected protocol, exposes external
interactive capabilities as unavailable, and the production runtime continues
without development authority.

#### Ordered runtime messages

After registration, these are the complete V1 authority messages. Every
identity below is in addition to the common envelope and must match the active
connection.

| Message and direction | Required payload and ordering |
| --- | --- |
| `yield_request` broker → runtime | Request/lease-candidate IDs, capability, hold ID, source registration/fingerprint, complete expected runtime/target/battle/frame/catalog binding, operator-control revision, last known runtime action sequence, and acknowledgement deadline. Only one may be pending; it precedes its `yield_ack` and any token issue. |
| `yield_ack` runtime → broker | Correlated request/hold IDs; `accepted` or stable rejection code; current operator state/revision; `in_flight_inputs=0`; installed external-hold identity; last completed runtime action sequence; fresh runtime/target/battle/frame/catalog binding; and acknowledgement time. An accepted acknowledgement is valid only after the App serializer has yielded as defined below. |
| `input_command` broker → runtime | Request/lease IDs and generation, operation ID, idempotency key and canonical command digest, exact catalog revision/digest, existing action ID plus bounded parameters, full expected source/runtime/target/battle/frame/state binding, dispatch-not-after time, result deadline, and the catalog-owned postcondition ID. At most one command is in flight, it follows an accepted yield, and it precedes its result or revocation barrier. |
| `input_result` runtime → broker | Operation ID and command digest; `not_dispatched`, `dispatched`, `no_op`, `failed`, or `outcome_unknown`; stable reason; runtime action sequence before/after; dispatch and completion times; catalog action ID; sanitized parameter summary; postcondition result; and resulting frame descriptor. The runtime records this before sending it. |
| `revoke_authority` broker → runtime | Request/lease IDs, incremented lease generation, reason, last operation ID permitted to finish, revocation deadline, and whether cleanup evaluation is required. It is a barrier: later input commands for that lease are invalid, and pending undispatched commands are discarded. |
| `cleanup_disposition` runtime → broker | Correlated revocation/hold IDs; `clean`, `production_cleanup`, `operator_required`, or `unknown`; whether the external hold was released; final runtime action sequence and in-flight count; authoritative state/frame binding when available; stable reason; and completion time. It is the terminal acknowledgement of revocation, not new input authority. |
| `runtime_status` runtime → broker | One-second heartbeat time; MainPID/session; operator state/revision; target/lock and acknowledged ADB target generation; battle/frame identities; action-catalog revision/digest plus the complete catalog object when that revision first appears; external hold and in-flight input summary; runtime action sequence; cleanup state; queue pressure; and a bounded digest/result summary of recent operation IDs needed for reconciliation. It grants no authority. |
| `broker_heartbeat` broker → runtime | One-second heartbeat time; service epoch; verified runtime session and acknowledged ADB target generation; active request/lease generation when any; broker queue pressure/audit health; and acknowledgement of the latest runtime sequence. It grants no authority. |
| `channel_shutdown` either direction | Initiator, stable reason, final local and acknowledged peer sequences, active hold/operation summary, cleanup disposition if known, whether reconnect is expected, and a two-second close deadline. The peer responds with its own shutdown frame when possible. |

`yield_ack`, `input_result`, and `cleanup_disposition` are created by the App
orchestrator, not inferred by the socket thread. Broker request state changes
still follow the canonical lifecycle below; runtime-peer message names are not
additional public request states.

#### Serialization, queues, and backpressure

The runtime socket receiver may authenticate, validate framing/order, and place
an immutable command in an App-owned mailbox. It must not import or call a tap,
swipe, ADB-input, handler, lifecycle, recovery, or worker callback. A wakeable
`App`/main orchestrator drains that mailbox at guarded action boundaries and
owns one production action serializer shared by existing handler, strategy,
auxiliary, lifecycle, blind-tapper, validation, and external-gateway input.
No production or external input path may bypass that serializer.

The orchestrator processes revocation/shutdown before normal commands, checks
the mailbox before every prospective production input, and does not wait for a
normal multi-second capture sleep to notice a peer command. To acknowledge
yield it stops the blind tapper, prevents new production producers, waits for
the serializer's current operation to finish, installs the distinct external
hold, captures/publishes fresh evidence, and emits `yield_ack` only with zero
input in flight. To execute `input_command`, the orchestrator rechecks every
binding and catalog guard under the same serializer, durably records intent,
dispatches at most once, records the result, and gives the socket writer the
immutable `input_result`.

Flow control is fail-closed and bounded:

- each peer's socket-writer normal queue holds at most 32 messages and
  256 KiB, plus a four-message control lane reserved for heartbeat,
  revocation/result, cleanup, and shutdown; the broker permits at most one
  yield and one input command outstanding;
- the runtime App mailbox holds at most 16 normal commands and 256 KiB, plus a
  four-message control lane reserved for revocation/shutdown;
- the broker runtime-event mailbox holds at most 32 normal events and 256 KiB,
  plus a four-message lane reserved for input result, cleanup, and shutdown;
- one lease permits at most 128 input operations; reaching that bound rejects
  another command with `lease_operation_limit` and begins normal completion
  or revocation;
- runtime status and broker heartbeat are latest-value coalesced rather than
  accumulated;
- both peers send their status/heartbeat every second; three seconds without a
  valid peer liveness frame, or any MainPID/lock mismatch, loses the channel
  and makes the runtime locally revoke external input;
- a yield uses the existing five-second acknowledgement deadline; an input
  must begin by its catalog-bounded dispatch deadline and report by its
  catalog-bounded action timeout plus two seconds; and
- full normal queues reject new external work with
  `runtime_backpressure`. An accepted input command is never silently dropped.
  Exhaustion of any reserved control lane closes the channel; the runtime
  preserves any operation result in its idempotency cache and applies local
  fail-closed revocation, while the broker treats an unreceived result as
  uncertain.

Each sender accepts only its next sequence. A repeated immediately prior
sequence with the identical canonical digest is a duplicate: the receiver does
not reapply it and returns/re-emits the cached correlated response. Reuse of a
sequence, message ID, idempotency key, or operation ID with different content,
or a gap/out-of-order sequence, is a protocol violation and closes the
channel. The runtime cache retains every operation digest/result for the
current nonterminal lease plus at least the most recent 256 terminal-lease
operations until ten minutes after their deadlines. The per-lease operation
cap makes that cache bounded. Reconnection never turns a duplicate into
another input.

#### Disconnect, restart, and uncertain-result behavior

Loss of the peer channel immediately makes interactive capabilities
unavailable and invalidates their external tokens at the broker. Exact failure
handling is:

- **Broker restart:** EOF invalidates the old service epoch. The runtime
  cancels undispatched external commands, permits only an already-started
  serialized input to finish once, records its result, retains the no-input
  external hold, and performs cleanup classification. It reconnects to the new
  runtime-peer socket and reports the old epoch, orphaned hold, cleanup
  disposition, and cached operation result in registration/status. The new
  broker issues no grant until reconciliation is safe.
- **Runtime restart:** peer loss and a changed systemd MainPID/session revoke
  all acknowledgements/tokens. A replacement runtime creates a new session and
  cannot inherit a hold, operation cache, or cleanup authority. If the broker
  cannot exclude possible old-process input, cleanup is `unknown`.
- **Same-epoch channel loss:** the broker revokes rather than preserves the
  lease. The still-running runtime locally blocks new external input, retains
  the hold through cleanup, and may reconnect with the same session only to
  reconcile sequences/results. Reactivation requires the public request
  lifecycle to issue a fresh token and lease generation.
- **Partial or malformed frame:** no partial frame dispatches. The receiver
  closes at the framing deadline. Any earlier complete input already handed to
  the App remains governed by its operation ID and cached result; the broker
  treats its outcome as uncertain until exact reconciliation.
- **Response loss after possible input:** the broker never blindly resends the
  command. On the same connection it may repeat the identical message solely
  to retrieve the runtime's cached result; after reconnect it uses the recent
  operation summary in `runtime_status`. A matching command digest and cached
  terminal result resolves the outcome without dispatch. Missing/mismatched
  evidence produces `outcome_unknown`, fails the request, retains the hold,
  and requires cleanup.
- **Orderly shutdown:** the initiator sends `channel_shutdown` only after
  revocation has begun. The channel closes after the peer response or the
  two-second deadline; lack of the response is handled as channel loss, never
  as proof of cleanup.

### Volatile and durable ownership

Socket paths, service epoch, active/queued request state, token material,
client heartbeat files, target-coordinator state, and published-frame bundles
are volatile and live only under `$XDG_RUNTIME_DIR/thetower`. A broker restart
must not restore an active token from disk.

Durable development-access audit evidence lives separately beneath
`$XDG_STATE_HOME/thetower/development-access`, falling back to
`$HOME/.local/state/thetower/development-access` when `XDG_STATE_HOME` is
unset. The directory is mode `0700` and audit segments are mode `0600`. No
worker checkout owns or rotates this ledger. Audit retention is a broker policy
advertised in status; the initial policy targets 180 days with a 1 GiB hard
cap, rotates only complete hash-chained segments, and reports when the size cap
shortens the time window. Expiry never runs while the newest segment or an
incident-pinned segment is involved.

### Identity namespaces

The implementation must not overload a generic “generation” field.

| Name | Meaning and change boundary |
| --- | --- |
| `service_epoch` | Random UUID created on every broker-service start. Every old request, token, status revision, and direct-read receipt is invalid in a new epoch. |
| `runtime_session_id` and `runtime_pid` | Production automation process identity. Both change on runtime replacement; a PID alone is never sufficient. |
| `runtime_action_sequence` | Production-owned monotonic input/yield sequence used to prove no input remains in flight. It is not a frame or lease generation. |
| `adb_target_generation` | Broker-owned monotonic target/ownership generation within one service epoch. It changes on endpoint selection, ownership handoff, or transport-owner replacement, whether or not the endpoint text is unchanged. |
| `battle_generation` | Broker-published monotonic lifecycle generation within one service epoch. Production advances it only with an authoritative boundary; continuity loss advances it to an explicitly unknown identity rather than reusing the prior battle. |
| `battle_identity` | Opaque verified identity/fingerprint for the current battle when available; otherwise explicitly `unknown`. It is not synthesized from a generation counter. |
| `frame_source_generation` | Monotonic within one service epoch and changes when the capture publisher, source geometry, target binding, or capture pipeline is replaced or reset. |
| `frame_sequence` | Strictly increasing successful-frame sequence within one frame-source generation. |
| `lease_generation` | Broker-owned monotonic grant/revocation counter within a service epoch. Every newly issued token gets a new value, including a resumed request. |
| `action_catalog_revision` | Positive revision of the exact production-installed semantic action catalog published by the current runtime. Its separately named SHA-256 binds catalog content; it is not a lease, source, or frame generation. |
| `development_source_fingerprint` | Exact registered Git/worktree state described above. It changes whenever relevant source state changes. |

Control request IDs, broker status revisions, input operation IDs, and audit
event sequences remain separately named counters or identities.

### Relationship to existing authority objects

The host-global development lease is not any existing in-process authority
object:

- `AuxiliaryRouteLease` in `core/action_authority.py` coordinates one
  production collector route. It carries no external token and cannot cross a
  process boundary.
- Runtime `AuthorityHold` values protect production-owned continuity,
  initialization, session preflight, and validation routes.
- Exclusive-validation receipts in `core/control_directives.py` authorize the
  profile-declared production Tournament validation sequence. Development
  cannot claim, reuse, complete, or inherit them.
- `SingleInstanceLock` currently derives its default path from a checkout.
  That remains useful process evidence during migration but can never serve as
  the host-global development coordinator.

Implementation adds a distinct suppressive
`external_development_authority` hold to the runtime. Unlike existing owner
holds, it must not make the generic `owner=...` decision path return allowed.
It blocks every production auxiliary, strategy, lifecycle, recovery, and blind
input route while observation continues. Only the separately verified
production input gateway may dispatch an external operation. Existing runtime
holds and profile receipts retain their own types and semantics.

## Capability model

### Capability and compatibility matrix

| Capability | Exclusive | Source registration | Direct ADB | UI/input scope | Boundary behavior |
| --- | --- | --- | --- | --- | --- |
| `published_frame_observation` | No | No for local read-only consumption | None | Immutable image and metadata only | Observer may continue; it must process changed target, battle, and frame metadata |
| `passive_device_read` | Shared, rate-limited | Yes | Exact-target `get-state` only | No navigation or input | One receipt/result; discarded on target-generation change |
| `coordinated_capture` | Shared capture slot | Yes | One exact-target screenshot only when explicitly granted | No navigation or input | One frame/result; never carries action authority |
| `interactive_running_battle` | Yes, host-global | Yes | Prohibited | Production-mediated, allowlisted actions within the verified same battle | Token ends at the boundary; eligible request may be suspended |
| `home_boundary` | Yes, host-global | Yes | Prohibited | Production-mediated Home work at verified `HOME/NEW_BATTLE`; cannot start a battle | Must return verified Home; production reruns normal gates |
| `owned_validation_battle` | Yes, host-global and separately authorized | Yes | Prohibited | One explicitly owned ordinary battle and its receipt-bound cleanup | Exact claimed battle only; never Tournament |

Compiled support is insufficient. Effective capability status also requires a
compatible broker and runtime protocol, fresh production owner evidence,
host-global coordinator ownership, and the capability-specific UI/target
preconditions. Status reports `compiled`, `available`, and an unavailable
reason separately.

### Direct-ADB policy

Development uses the following order:

1. tracked retained fixtures;
2. freely consumed atomic production frames;
3. a broker-coalesced fresh production capture;
4. a one-shot coordinated direct capture only when the first three cannot
   answer the stated question; and
5. exclusive production-mediated input only under an active lease.

The initial direct-read limits are normative defaults and are advertised so a
future tighter policy does not require a client guess:

| Operation | Limit | Coordination and audit |
| --- | --- | --- |
| Published-frame read | No broker rate limit | No per-read audit; publication and retention events are audited |
| Exact `adb -s TARGET get-state` | One per source every 5 seconds, at most two host-wide per second, 250 ms coalescing window | One five-second receipt; target generation checked before and after; request/result audited |
| Exact screenshot | One per source every 10 seconds, at most one host-wide every 2 seconds, 500 ms coalescing window | Shared capture slot; target generation checked before and after; request/grant/result audited |

Broker-mediated publication is always preferred for a screenshot. A direct
screenshot is justified only when the latest complete publication misses the
request's declared freshness bound or when the task is specifically validating
the capture transport/encoding path. The broker issues a five-second one-shot
receipt naming the exact target, target generation, only permitted argv shape,
and ten-second command deadline. The permitted operation is equivalent to
`adb -s TARGET exec-out screencap -p`; it cannot add `shell`, a pipe, redirection,
or another subcommand. The client must submit the completion and captured hash,
then recheck the target generation. A missing completion is audited and
rate-limited like a failed capture.

Development must never invoke `adb connect`, `disconnect`, `reconnect`,
`start-server`, `kill-server`, `forward`, or `reverse`; select another target;
pull arbitrary device files; foreground/restart the app; or send `adb shell
input`. Those remain production-coordinator operations. No direct-read receipt
can be upgraded into input authority.

### Production action catalog and mediated input

All state-changing development operations go to the production input gateway.
V1 accepts only a stable action ID already present in the exact
production-installed semantic action catalog published by the authenticated
runtime peer. It does not accept a shell or raw ADB command, arbitrary
executable, generic action kind, clickmap/template path, asset bytes or digest
supplied by a worker, blind tap, unbounded coordinate, Python callback, or
worker-defined postcondition.

The runtime builds and validates the catalog only from code, configuration, and
assets installed in the production checkout. It publishes the complete catalog
through `runtime_register` and, whenever the catalog revision changes, in
`runtime_status`. The broker verifies its digest and advertises only the
effective catalog from the authenticated current runtime. The V1 catalog must
fit in one 64 KiB peer frame; an oversized catalog makes catalog/input
capabilities unavailable until a later chunking-capable protocol is
negotiated. Schema 1 is:

```json
{
  "action_catalog_schema": 1,
  "action_catalog_revision": 12,
  "action_catalog_digest": "sha256-of-canonical-catalog",
  "production_commit": "git-object-id",
  "actions": [
    {
      "action_id": "battle.menu.open_perks.v1",
      "capabilities": ["interactive_running_battle"],
      "allowed_states": {
        "primary": ["RUNNING"],
        "secondary": [],
        "overlays": []
      },
      "guard": {
        "guard_id": "production.visible_clickmap_label.v1",
        "guard_revision": 3
      },
      "parameters": {
        "json_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": false
        },
        "maximum_encoded_bytes": 256
      },
      "postcondition": {
        "postcondition_id": "production.perks_menu_visible.v1",
        "timeout_milliseconds": 3000
      },
      "production_dependencies": [
        {
          "kind": "clickmap_entry",
          "logical_id": "battle.buttons.perks",
          "sha256": "production-digest"
        },
        {
          "kind": "match_template",
          "logical_id": "menus.perks",
          "sha256": "production-digest"
        }
      ],
      "rollout": {
        "enabled": true,
        "request_allowlist": "development-running-v1"
      }
    }
  ]
}
```

Every entry names its allowed capability, primary/secondary/overlay states,
production guard identity and revision, closed parameter schema and encoded
size/range/enum bounds, fixed production-owned postcondition and timeout,
digests for every applicable production asset/config dependency, and
disabled/allowlisted rollout state. Empty parameters are still explicitly
closed as in the example. Catalog revision increases whenever any entry,
guard, bound, dependency digest, postcondition, or rollout state changes; the
catalog digest covers canonical JSON excluding only its digest field. A stable
`action_id` is 1–128 lowercase ASCII letters, digits, dots, underscores, or
hyphens, cannot encode a filesystem path, and is never reused for incompatible
semantics. Such a change receives a new versioned ID.

At lease-request time the worker supplies an expected catalog revision/digest
and a nonempty set of at most 32 existing action IDs. The broker rejects an
absent, disabled, capability-incompatible action, or one incompatible with the
request's declared states before queueing/activation. Each input then selects
exactly one ID from that granted set; when accepting that input request, the
broker again checks the revision/digest, parameter bounds, expected state, and
fixed postcondition. At dispatch time the runtime independently requires the
same revision/digest, resolves the action ID only in its in-memory
production-installed catalog, revalidates the production dependency digests,
parameter bounds, current state, and guard, then runs the catalog-owned
postcondition. A changed catalog invalidates pending input and returns
`action_catalog_changed`; it is never silently rebound to the new revision.

Source registration identifies and audits the requesting development state. It
does not authorize the broker/runtime input path to import, execute, resolve,
or read worker code, templates, clickmap/config paths, callbacks, or assets.
The source registrar may perform only its separately specified Git and content
hashing to establish the fingerprint. No worker path or content digest becomes
an action-catalog dependency. Runtime-peer yield/input messages carry only the
opaque source registration ID and fingerprint, never a worker path or worker
content.

A new worker matcher, template, or action is developed and tested offline
against tracked retained fixtures or copies of atomically published frames.
Novel live behavior becomes requestable only after master review, integration
into `develop`, the combined gate, promotion to `main`, production deployment
behind its disabled/allowlisted rollout boundary, and publication by the
running production runtime in a new catalog revision. Until all steps complete,
the broker reports the action unavailable regardless of the worker source
fingerprint.

Every operation supplies:

- an active token and lease generation;
- a unique idempotency/operation ID;
- the complete current binding tuple;
- the exact action-catalog revision and digest;
- one existing capability-compatible stable action ID and bounded parameters;
- an exact expected frame-source generation and frame sequence; and
- an expected primary state, battle identity, and optional catalog-allowed
  temporary state.

The gateway synchronizes operator control, source state, lease state, runtime
owner, target generation, battle generation/identity, frame freshness, catalog,
expected screen, and catalog guard immediately before dispatch. It writes and
syncs the durable action intent before input. It records the individual input
and result through the normal production action-log contract and the
development audit ledger. The result includes the production input sequence and
a fresh post-action frame or an explicit reason why the catalog postcondition
was not proven.

An input response lost after dispatch is resolved by the runtime-peer operation
cache and idempotency record. A duplicate returns the original result and never
dispatches twice. If the broker cannot prove whether dispatch occurred, it
fails the request and requires cleanup rather than retrying.

### Explicitly owned validation battles

`owned_validation_battle` requires an operator/master authorization created
outside the worker request. The authorization names the source fingerprint,
purpose, target generation, allowed ordinary battle kind, maximum duration,
whether receipt-bound Surrender cleanup is permitted, and expiry. A Home lease
alone cannot create this authorization.

At fresh verified `HOME/NEW_BATTLE`, the broker atomically claims a new
development-validation receipt before the production gateway presses the
verified ordinary New Battle control. The receipt binds the service epoch,
runtime session/PID, target generation, source fingerprint, request and lease,
pre-battle frame, and later observed battle generation/identity. It has its own
`authorized -> claimed -> running -> cleanup -> completed/failed` lifecycle and
is never stored in or treated as the existing Tournament exclusive-validation
ledger.

Only that same receipt and owner may request allowed cleanup. Surrender, when
the authorization explicitly permits it, additionally requires fresh running
evidence that excludes Tournament identity. Owner change, resumed or
pre-existing battle, ambiguous identity, process/broker restart, target change,
or heartbeat loss removes Surrender authority. Failure closes without input.

## Request and lease lifecycle

### Request states

The exact wire states are:

| State | Meaning |
| --- | --- |
| `requested` | The broker durably audited a syntactically valid, idempotent request and is performing source/capability validation. |
| `queued` | The request is valid and waiting for an eligible production boundary and the exclusive scheduler or shared rate limiter. |
| `acknowledged` | The capability coordinator reserved the exact shared or exclusive authority and proved its prerequisites. For an interactive request, production installed the external hold and yielded; for a one-shot read/capture, the broker reserved the target-bound shared slot. No token or receipt has yet been used. |
| `active` | A new short-lived interactive token or one-shot read/capture receipt is valid for the named capability and binding tuple. Only an interactive token can authorize mediated input. |
| `suspended` | The token is revoked, production has boundary authority, and an explicitly eligible continuation request is retained without input authority. |
| `revoked` | Authority ended safely because of cancellation, supersession, source/owner change, Pause, or another fail-closed event. |
| `expired` | A request, acknowledgement, lease, heartbeat, or hard deadline elapsed. |
| `completed` | The one-shot receipt or interactive token ended and the responsible coordinator verified the required result; interactive completion also proves its postcondition/cleanup. |
| `failed` | Establishment, dispatch certainty, revocation, or cleanup could not be proven safe. Further interactive grants remain blocked when cleanup is unresolved. |

Only these transitions are valid:

| From | To | Required cause |
| --- | --- | --- |
| `requested` | `queued` | Source, compatibility, authorization, and request validation passed |
| `requested` | `failed` / `expired` / `revoked` | Validation failure, request deadline, or client cancellation |
| `queued` | `acknowledged` | The shared coordinator reserved the exact one-shot slot, or the interactive scheduler received a fresh production-yield acknowledgement |
| `queued` | `failed` / `expired` / `revoked` | Source/capability loss, wait deadline, cancellation, or supersession |
| `acknowledged` | `active` | Broker revalidated every binding, synced the grant audit, and issued a new token or one-shot receipt |
| `acknowledged` | `queued` / `suspended` | Eligibility changed before token issue without unsafe external input |
| `acknowledged` | `failed` / `expired` / `revoked` | Binding loss, acknowledgement deadline, cancellation, or safety failure |
| `active` | `suspended` | Natural boundary or operator Pause with an eligible continuation policy; old token is already invalid |
| `active` | `completed` | Client completion followed by verified cleanup/postcondition |
| `active` | `revoked` / `expired` / `failed` | Safe revocation, lease/heartbeat deadline, or uncertain dispatch/cleanup |
| `suspended` | `queued` | Continuation policy is still eligible and its required fresh boundary appears |
| `suspended` | `revoked` / `expired` / `failed` | Cancellation, source drift, hard deadline, incompatible restart, or cleanup failure |

`revoked`, `expired`, `completed`, and `failed` are terminal. Interactive
resumption always returns through `queued`, production acknowledgement, and a
new lease generation/token.

Passive reads and captures use the same state vocabulary for observable
coordination, but never install the external hold, enter `suspended`, or carry
input authority. They normally take the short path `requested -> queued ->
acknowledged -> active -> completed`; coalescing may return an already
completed compatible result. Their active receipt ends after one invocation
and cannot be renewed. Every transition out of an interactive `acknowledged`
or `active` state first invalidates the prospective/current token and obtains
the runtime's cleanup/hold disposition before another exclusive grant.

### Queueing, fairness, and idempotency

Published-frame reads do not enter a queue. Passive device reads and captures
use their shared rate/coalescing queues. All three interactive capabilities
share one host-global exclusive queue.

The exclusive scheduler selects the oldest currently eligible request.
Ineligible Home or running-battle requests do not block an eligible older
boundary class forever; original enqueue time is retained when a suspended
request becomes eligible. Clients cannot assign themselves a priority. The
master/operator may explicitly cancel or reorder a request, which creates an
audit event. One source registration may have only one outstanding interactive
request unless the first is terminal.

`client_request_id` and `idempotency_key` are scoped to the Unix principal and
source registration. Repeating an identical body returns the same request or
operation. Reusing the key with different canonical content returns
`idempotency_conflict`. Queue wait defaults to ten minutes and may be requested
up to a hard thirty-minute limit.

### Deadlines, heartbeat, and renewal

An acknowledgement expires after five seconds if no token or receipt is
issued. A one-shot passive receipt expires after five seconds for `get-state`
or ten seconds for capture, permits no heartbeat, and completes or fails on its
single result. An interactive token has a ten-second rolling lease and requires
a heartbeat at least every three seconds. A valid heartbeat may extend the
rolling expiry but never the hard active limit:

| Capability | Default hard active limit | Maximum |
| --- | --- | --- |
| `interactive_running_battle` | 2 minutes | 5 minutes |
| `home_boundary` | 1 minute | 2 minutes |
| `owned_validation_battle` | 5 minutes | 15 minutes, bounded by its operator authorization |

Heartbeat and renewal recheck service epoch, source registration, operator
control, runtime session/PID, target generation, battle identity/generation,
frame source, action-catalog revision/digest, request state, and client
principal. A heartbeat is not input authority. A suspended Game Over
continuation requires the client to keep a separate request heartbeat; it
never keeps the old token alive.

Pause-triggered suspension never automatically reactivates on Resume. The
client must explicitly requeue after the operator's Resume is acknowledged.
A request that opted into `next_stable_running` continuation at natural Game
Over may requeue automatically after the complete terminal/Home/startup
sequence described below.

### Lease and token schema

The active interactive grant response contains:

```json
{
  "schema_version": 1,
  "request_id": "opaque",
  "lease_id": "opaque",
  "lease_generation": 42,
  "capability": "interactive_running_battle",
  "allowed_action_ids": ["battle.menu.open_perks.v1"],
  "lease_token": "one-time-visible-opaque-secret",
  "issued_at": "RFC3339",
  "heartbeat_due_at": "RFC3339",
  "expires_at": "RFC3339",
  "hard_deadline_at": "RFC3339",
  "bindings": {
    "service_epoch": "uuid",
    "runtime_session_id": "opaque",
    "runtime_pid": 1234,
    "runtime_action_sequence": 77,
    "adb_target": "localhost:5555",
    "adb_target_generation": 9,
    "battle_generation": 31,
    "battle_identity": "opaque-or-unknown",
    "frame_source_generation": 4,
    "activation_frame_sequence": 880,
    "action_catalog_revision": 12,
    "action_catalog_digest": "sha256",
    "source_registration_id": "opaque",
    "development_source_fingerprint": "sha256"
  }
}
```

Tokens contain at least 256 bits of randomness, are returned only through the
mode-`0600` Unix socket, and are compared by a keyed digest. Plain token
material may exist only in process memory or a mode-`0600` client file beneath
`$XDG_RUNTIME_DIR/thetower/clients`. It never appears in a URL, status
snapshot, log, audit event, frame metadata, worktree, shell history example, or
durable state. Revocation increments the lease generation before cleanup.

### Production yield acknowledgement

An interactive request cannot become `acknowledged` merely because the broker
selected it. The production runtime must:

1. synchronize the current operator control revision and reject `PAUSED` or
   `STOPPED`;
2. wait for any already-dispatched guarded input to finish without starting a
   new route;
3. cooperatively stop the blind tapper and prevent new auxiliary, strategy,
   lifecycle, recovery, initialization, preflight, and handler input;
4. install the exact suppressive external-development hold;
5. publish a fresh complete frame and current lifecycle/target identity; and
6. acknowledge the request with `in_flight_inputs=0`, the last completed
   runtime action sequence, hold ID, and complete binding tuple.

The broker accepts the acknowledgement only while its publisher matches the
current systemd MainPID, runtime session, host-global target owner, service
epoch, and request. It then rechecks source state and operator control, writes
and syncs the grant audit, and issues the token. The input gateway repeats all
material checks before every operation.

A passive read/capture acknowledgement does not install an external hold or
claim that production yielded. It requires the broker to reserve the shared
coordinator slot, recheck the exact target generation, rate/coalescing policy,
source identity, and command allowlist, then sync the receipt audit before
activation.

### Operator Pause precedence

The external hold is not `PAUSED` and never changes
`logs/automation_ctl.json` or `AUTOMATION.state`. The normal control file
remains authoritative operator intent.

When Pause or Stop is requested:

- the gateway rejects new operations immediately;
- the broker increments the interactive lease generation and invalidates its
  token;
- an eligible request becomes `suspended`, otherwise `revoked`;
- the runtime applies and acknowledges the operator directive through its
  normal path; and
- no development cleanup input occurs while Pause or Stop remains in effect.

Published observation, detection, and a purely passive shared read/capture may
continue while paused when their own target and rate guards pass; they cannot
delay the operator directive or acquire input authority.

If the last external action left a non-authoritative screen, the external hold
may remain as a no-input `cleanup_required` condition beneath Pause. Resume does
not clear it. After explicit Resume, production must first re-establish a
verified safe screen or obtain operator direction before ordinary actions or a
new development grant.

## Atomic production frame publication

### Bundle format

The production runtime is the normal publisher. Bundles are host-global and
immutable:

```text
$XDG_RUNTIME_DIR/thetower/frames/<target-key>/
    publication-status.json
    current.json
    sources/<frame-source-generation>/<frame-sequence>/
        frame.png
        metadata.json
```

`frame.png` is the complete canonical `1080x1920` PNG accepted by the runtime's
capture completeness guard. `metadata.json` schema 1 contains:

- service epoch, runtime session/PID, runtime action sequence;
- exact ADB target and target generation;
- battle generation and nullable/unknown battle identity;
- frame-source generation and frame sequence;
- wall-clock and monotonic capture start/completion times;
- native geometry and canonical geometry;
- primary state, secondary states, overlays, state-detector revision, and
  whether the state is authoritative or ambiguous;
- encoded byte length and SHA-256; and
- publication schema/capability revisions.

A capture failure never republishes the previous image with a new timestamp.
It atomically updates `publication-status.json` with the failed-attempt time,
reason, invalidation state, and last-good bundle identity. Consumers may use a
last-good frame as explicitly stale evidence but not as current action
authority.

### Commit protocol

For each successful frame, the publisher:

1. allocates the next sequence while holding the publisher lock;
2. creates a no-follow sibling staging directory;
3. writes `frame.png` and `metadata.json`, flushes and `fsync`s both, and
   verifies the encoded hash and metadata identity;
4. `fsync`s the staging directory;
5. atomically renames the complete directory to its immutable final name and
   `fsync`s the source directory;
6. writes a same-directory staged `current.json` containing the exact bundle
   identity and metadata/hash summary, flushes and `fsync`s it, atomically
   replaces the pointer, and `fsync`s the target directory; and
7. atomically refreshes `publication-status.json`.

An existing immutable bundle is never overwritten. Duplicate sequence,
symlink, identity mismatch, partial stage, or directory-sync failure makes the
attempt unavailable and leaves the former pointer intact but stale. Startup
may remove only broker-owned incomplete staging names after auditing them.

### Reader protocol and retention

A reader loads `current.json`, opens the referenced immutable metadata and
image without following symlinks, verifies that every identity agrees, checks
the byte length/hash, and applies its requested freshness bound. A reader that
needs “latest at completion” rereads `current.json` after opening the bundle;
either immutable version is internally consistent, but only an unchanged
pointer proves it was still latest.

Passive observers may remain attached across battle boundaries. They must reset
battle-local derivations whenever target generation, battle generation,
battle identity, or frame-source generation changes. A sequence gap means
frames were skipped, not that state was continuous.

The initial retention policy keeps at most 32 complete bundles per target, no
bundle older than ten minutes except the current bundle, and no more than
256 MiB host-wide. The broker removes oldest non-current bundles after a newer
pointer is durable. Open files remain safe under normal POSIX unlink semantics.
Retention limits and current usage are advertised. A development client that
needs durable evidence must deliberately copy and register it; it cannot pin
the volatile publication tree indefinitely.

An action operation names one exact expected frame sequence. A continuing
lease may observe later sequences in the same source generation, but each
input rebinds to the submitted exact sequence and a production-fresh
precondition. The activation frame is not timeless action authority.

## UI and lifecycle authority

### State behavior matrix

| Production state | Published observation | New interactive authority | Active interactive behavior | Production authority |
| --- | --- | --- | --- | --- |
| Stable `RUNNING` battle | Publish normally | Running-battle request may be acknowledged after stable readiness | Allow only the declared same-battle state set and gateway actions | External hold suppresses input; capture/detection continue |
| Declared temporary menu in same battle | Publish changed state | No new default-running grant | Keep only when the request explicitly allowlists the state/route; every action names that state | Remains yielded while route is valid |
| Unexpected navigation or ambiguous/`UNKNOWN` screen | Publish ambiguous/invalidation status | None | Revoke immediately; no cleanup input until a safe route is proven | Hold becomes `cleanup_required`; no generic recovery race |
| Authoritative natural `GAME_OVER` or `TOURNAMENT_RESULTS` | Publish boundary with new lifecycle metadata | None until boundary policy below | Token revoked; eligible request suspended | Terminal-processing authority returns to production |
| Verified `HOME/NEW_BATTLE` | Publish Home boundary | Oldest eligible Home request may be acknowledged before normal Home actions | Home gateway actions only; New Battle prohibited except owned-validation receipt | Otherwise production owns gates/start |
| Home `RESUME_BATTLE` | Publish same-battle navigation | No Home-boundary grant | Running lease is not inferred or restored from Home | Production preserves existing battle identity |
| Run initialization or session preflight | Publish normally | None | Any old token is invalid | Existing production hold owns its bounded route |
| Operator `PAUSED` or `STOPPED` | Observation may continue | None | Token invalid; no development input | Operator state is authoritative |

Stable running-battle readiness requires two consecutive complete publications
from the same runtime session, target generation, battle generation/identity,
and frame-source generation; both must authoritatively report `RUNNING`, no
exclusive production hold, no target handoff, no unresolved cleanup, and
acknowledged operator `RUNNING` intent. The broker advertises the actual
stability interval and requires at least one normal capture cadence between the
two frames.

Temporary menus never change battle identity by themselves. The request must
declare an allowlisted route and expected states before activation. Entering a
recognized but undeclared menu suspends input and normally revokes the lease;
an unexpected state always revokes it. Generic recovery cannot act behind an
external client.

### Natural Game Over and the next battle

At authoritative natural Game Over during interactive development access, the
order is fixed:

1. production publishes the terminal boundary;
2. the broker increments the lease generation, revokes the active token, and
   moves only an opted-in eligible request to `suspended`;
3. the runtime removes external input authority and returns terminal-processing
   authority to the normal production handler;
4. production completes its normal record, Game Over policy, and required
   terminal work without a development client choosing Retry or Home;
5. when a ready Home-boundary request exists and operator control permits
   navigation, production itself selects its verified Home terminal route;
   `PAUSED`, `STOPPED`, and operator `WAIT` continue to win, and the external
   client sends no terminal input;
6. at verified `HOME/NEW_BATTLE`, the broker services the oldest ready
   `home_boundary` request, if any;
7. after any Home request completes—or immediately when none is ready—
   production performs its normal gates, starts the next battle, and completes
   run initialization and session preflight; and
8. only after fresh stable running-battle readiness may an eligible suspended
   `next_stable_running` request return through `queued -> acknowledged ->
   active` with a new token and the new battle/frame identities.

A suspended request cannot influence terminal mode, choose a boundary, skip
gates, retain its old frame, or keep its old token. A Tournament terminal never
becomes an ordinary validation-battle cleanup opportunity.

### Home completion and cleanup

A `home_boundary` client must complete on a fresh verified
`HOME/NEW_BATTLE` frame with the same target generation. After token
revocation, production re-detects Home and reruns every applicable normal
profile gate; a client claim that configuration is correct is not gate
evidence. Home `RESUME_BATTLE`, Workshop, another screen, or ambiguous evidence
does not satisfy cleanup.

An `interactive_running_battle` client normally completes on fresh same-battle
`RUNNING` evidence. A declared temporary route must restore its source state.
It cannot complete by causing a boundary. An owned validation battle follows
its separate receipt and authorization.

### Fail-closed cleanup

Revocation first invalidates the token and blocks all further external input.
Production then classifies cleanup:

- `clean`: no external input was dispatched, or a fresh frame proves the
  capability's required source/postcondition;
- `production_cleanup`: a predeclared, production-owned cleanup route is safe
  and its own guards succeed;
- `operator_required`: state is recognized but no automatic cleanup is
  authorized; or
- `unknown`: screen, owner, dispatch result, or identity is ambiguous.

Only `clean` or successful `production_cleanup` releases the external hold and
allows another interactive request. `operator_required` and `unknown` set the
request to `failed`, retain a no-input cleanup hold, publish a prominent status,
and require operator resolution. Cleanup never borrows authority from the
expired/revoked token. Pause can always be applied but does not declare cleanup
successful.

## Failure and restart behavior

| Event | Required behavior |
| --- | --- |
| Broker/control-service restart | New service epoch; all tokens, direct-read receipts, acknowledgements, queued and suspended requests are invalid. Clients register and request again. The surviving runtime follows the peer-channel broker-restart sequence: reject old-epoch input, retain the no-input hold, reconnect, and report orphan/operation/cleanup evidence before any new grant. |
| Production runtime restart or PID change | Peer-credential/MainPID loss revokes acknowledged/active leases and fails any owned-validation receipt that cannot prove its old owner. A replacement creates a new session and cannot inherit external yield, operation replay, or cleanup authority. |
| Production runtime crash while externally yielded | Broker invalidates token immediately. The next runtime starts with no external authority and must verify current UI/target before normal actions; uncertainty publishes `cleanup_required`. |
| Runtime-peer channel loss or partial/malformed frame | Stop accepting/dispatching external input, revoke the public lease, retain the external hold through cleanup, and reconcile only through a newly authenticated ordered channel. A partial frame is never dispatched. |
| Input result or acknowledgement loss | Never infer success and never blindly redispatch. Resolve only from the exact operation digest and cached runtime result; otherwise return `dispatch_outcome_unknown` and require cleanup. |
| Client exit or heartbeat loss | Expire token, stop gateway input, and run the fail-closed cleanup classification. The client's process PID is diagnostic, not the sole identity. |
| ADB target generation change | Discard in-flight reads/captures, revoke every interactive lease, invalidate published-current action authority, and require new source registration/request expectations. |
| ADB disconnect without target change | Mark publication unavailable/stale. No input or direct capture is granted; an active lease is suspended briefly only if no input uncertainty exists, otherwise revoked. Production alone reconnects. |
| Frame publisher restart/source change | Increment frame-source generation. Passive observers reset derived state; active operations using the old source fail `frame_generation_changed`. |
| Battle identity/generation change | Passive subscription survives with changed metadata. Active token is revoked; only the explicit natural-boundary suspension policy may retain the request. |
| Development source drift | Revoke before further input; no automatic re-registration or token inheritance. |
| Durable audit failure before input | Reject the input. Audit recovery is required before another state-changing operation. |
| Result-audit failure after possible input | Treat dispatch as uncertain, fail the request, and require cleanup; never retry blindly. |
| Cleanup failure | Keep the external no-input hold and block the exclusive queue until production or the operator proves a safe boundary. |

The broker may keep passive frame service available when automation is absent
only by marking the producer inactive and every retained frame stale. No stale
frame, old systemd PID, repository-local lock, or broker queue entry can
manufacture a live runtime owner.

## Additive API, status, and CLI

### Transport and compatibility

Development endpoints are additive `/api/v1/development/...` resources served
only on `$XDG_RUNTIME_DIR/thetower/development-broker.sock`. The existing
loopback HTTP API may publish a non-secret summary, revision, and unavailable
reason, but it must not issue tokens, accept development input, or expose
source paths through the Windows tunnel. The external API cannot upgrade its
connection to the framed `runtime-peer.sock` protocol.

The service increments its monotonic server revision and advertises these
independently gated capabilities as they land:

- `development_broker_v1`;
- `atomic_frame_publication_v1`;
- `development_source_fingerprint_v1`;
- `coordinated_development_capture_v1`;
- `runtime_peer_channel_v1`;
- `external_development_authority_v1`;
- `development_action_catalog_v1`;
- `development_input_gateway_v1`;
- `development_boundary_continuation_v1`; and
- `owned_development_validation_battle_v1`.

The production runtime separately advertises its broker protocol revision and
effective capabilities. A client requires API version 1, its compiled minimum
server revision, every capability it uses, and an overlapping runtime protocol.
Unknown additive response fields are ignored. Unknown request fields, state
values, action IDs, parameter fields, and capability names are rejected.
Compatibility failure is read-only and cannot create or retain a lease.

### Resources

| Method | Resource | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/development/status` | Effective capabilities, service/runtime/target/battle/frame identities, operator acknowledgement, queue summaries, active lease summary without token, cleanup state, rate policy, and audit health |
| `POST` | `/api/v1/development/sources` | Independently inspect and register one worktree/source fingerprint |
| `POST` | `/api/v1/development/requests` | Create an idempotent passive-read or exclusive request |
| `GET` | `/api/v1/development/requests/{request_id}` | Read state, revision, queue position, deadlines, bindings, and reason |
| `POST` | `/api/v1/development/requests/{request_id}/heartbeat` | Renew an active rolling lease or maintain an eligible suspended request |
| `POST` | `/api/v1/development/requests/{request_id}/resume` | Explicitly requeue a Pause-suspended request after Resume acknowledgement |
| `POST` | `/api/v1/development/requests/{request_id}/complete` | Revoke token and ask production to verify the required postcondition |
| `POST` | `/api/v1/development/requests/{request_id}/cancel` | Cancel without granting cleanup input |
| `GET` | `/api/v1/development/frames/current` | Read the current pointer/metadata or a stale/unavailable disposition |
| `GET` | `/api/v1/development/frames/{source_generation}/{sequence}/image` | Read one immutable published image |
| `GET` | `/api/v1/development/actions` | Read the current runtime-published production action catalog and effective rollout availability |
| `POST` | `/api/v1/development/captures` | Request a coalesced publisher capture or justified one-shot direct-capture receipt |
| `POST` | `/api/v1/development/input` | Submit one idempotent production-mediated semantic action under an active lease |

Every mutable response includes `request_revision` or `broker_revision` so a
client can detect lost updates. Mutations that resolve a request accept the
expected revision and fail conflict when stale.

### Request and status shapes

Source registration request and response are shaped as:

```json
{
  "schema_version": 1,
  "client_request_id": "uuid",
  "worktree_path": "/allowlisted/canonical/worktree",
  "expected_branch": "feature/example",
  "expected_head": "git-object-id"
}
```

```json
{
  "schema_version": 1,
  "source_registration_id": "opaque",
  "repository_id": "broker-assigned",
  "worktree_path": "/verified/canonical/worktree",
  "branch": "feature/example",
  "head": "git-object-id",
  "dirty": true,
  "development_source_fingerprint": "sha256",
  "created_at": "RFC3339",
  "expires_at": "RFC3339"
}
```

`expected_branch` and `expected_head` are optimistic client checks. The broker
computes and returns every authoritative source field; it never accepts a
claimed fingerprint.

An exclusive request is shaped as:

```json
{
  "schema_version": 1,
  "client_request_id": "uuid",
  "idempotency_key": "opaque",
  "source_registration_id": "opaque",
  "development_source_fingerprint": "sha256",
  "capability": "interactive_running_battle",
  "reason": "bounded human-readable purpose",
  "requested_active_seconds": 120,
  "continuation": "none",
  "allowed_ui_states": ["RUNNING"],
  "requested_action_ids": ["battle.menu.open_perks.v1"],
  "expected": {
    "service_epoch": "uuid",
    "adb_target_generation": 9,
    "battle_generation": 31,
    "battle_identity": "opaque",
    "action_catalog_revision": 12,
    "action_catalog_digest": "sha256"
  }
}
```

`continuation` is one of `none`, `next_stable_running`, or `home_boundary` and
must be compatible with the capability. The broker response always includes
the request ID, exact state, state reason/code, request revision, queue
position when applicable, created/updated/deadline times, source identity, and
current bindings. Only an active interactive response includes the lease token
schema above.

A passive device-read request and its active one-shot receipt are shaped as:

```json
{
  "schema_version": 1,
  "client_request_id": "uuid",
  "idempotency_key": "opaque",
  "source_registration_id": "opaque",
  "development_source_fingerprint": "sha256",
  "capability": "passive_device_read",
  "operation": "adb_get_state",
  "reason": "bounded human-readable purpose",
  "expected_adb_target_generation": 9
}
```

```json
{
  "schema_version": 1,
  "request_id": "opaque",
  "state": "active",
  "direct_read_receipt": {
    "receipt_id": "opaque",
    "adb_target": "localhost:5555",
    "adb_target_generation": 9,
    "argv": ["adb", "-s", "localhost:5555", "get-state"],
    "issued_at": "RFC3339",
    "expires_at": "RFC3339"
  }
}
```

The client submits the bounded exit/result summary to the `complete` resource.
The broker rechecks target generation before accepting `completed`; target
change, timeout, or missing completion fails or expires the request and the
reported result is discarded.

A coordinated capture request and response are shaped as:

```json
{
  "schema_version": 1,
  "client_request_id": "uuid",
  "idempotency_key": "opaque",
  "source_registration_id": "opaque",
  "development_source_fingerprint": "sha256",
  "reason": "fresh capture needed for capture-path validation",
  "freshness_milliseconds": 1000,
  "mode": "published_or_broker",
  "expected_adb_target_generation": 9
}
```

```json
{
  "schema_version": 1,
  "request_id": "opaque",
  "disposition": "published",
  "coalesced": true,
  "adb_target_generation": 9,
  "frame": {
    "frame_source_generation": 4,
    "frame_sequence": 884,
    "captured_at": "RFC3339",
    "sha256": "sha256"
  },
  "direct_capture_receipt": null
}
```

`mode` is `published_or_broker` by default. The separately allowlisted
`direct_if_justified` mode returns a one-shot receipt instead of a frame only
after applying the direct-capture justification and rate policy; the response
then enters `active` and names its receipt ID, exact fixed argv template,
issue/expiry times, and target generation. It completes through the same
bounded result and post-target-generation check as a passive device read.

Development status is one atomic logical snapshot. This unavailable example
uses illustrative positive revisions:

```json
{
  "api_version": 1,
  "server_revision": 26,
  "capabilities": [],
  "development_broker": {
    "schema_version": 1,
    "broker_revision": 184,
    "service_epoch": "uuid",
    "runtime": {
      "active": false,
      "runtime_session_id": null,
      "runtime_pid": null,
      "protocol_revision": null,
      "peer_authenticated": false,
      "yield": null
    },
    "operator_control": {
      "requested_state": "UNKNOWN",
      "request_id": null,
      "acknowledged": false
    },
    "adb": {
      "target": null,
      "target_generation": null,
      "available": false
    },
    "battle": {
      "generation": null,
      "identity": "unknown",
      "state": "UNKNOWN",
      "stable_running": false
    },
    "frame": {
      "source_generation": null,
      "sequence": null,
      "fresh": false
    },
    "action_catalog": {
      "revision": null,
      "digest": null,
      "available_actions": []
    },
    "exclusive_queue": [],
    "active_lease": null,
    "cleanup": {"required": false},
    "audit": {"healthy": true}
  }
}
```

The broker atomically publishes a mode-`0600`
`$XDG_RUNTIME_DIR/thetower/development-status.json` mirror after each revision
for local diagnostics. The Unix-socket API is canonical; a partially written,
wrong-epoch, or stale mirror has no authority.

### Input shape

```json
{
  "schema_version": 1,
  "request_id": "opaque",
  "lease_id": "opaque",
  "lease_generation": 42,
  "lease_token": "opaque-secret",
  "operation_id": "uuid",
  "idempotency_key": "opaque",
  "expected": {
    "service_epoch": "uuid",
    "runtime_session_id": "opaque",
    "runtime_pid": 1234,
    "adb_target_generation": 9,
    "battle_generation": 31,
    "battle_identity": "opaque",
    "frame_source_generation": 4,
    "frame_sequence": 884,
    "action_catalog_revision": 12,
    "action_catalog_digest": "sha256",
    "primary_state": "RUNNING"
  },
  "action": {
    "action_id": "battle.menu.open_perks.v1",
    "parameters": {}
  }
}
```

The response distinguishes `accepted`, `not_dispatched`, `dispatched`,
`no_op`, `failed`, and `outcome_unknown`, and includes the stable operation
ID, runtime action sequence before/after, dispatch time, catalog action ID,
sanitized parameter summary, postcondition status, and resulting frame
descriptor. “Accepted” alone is never presented as evidence that input
occurred.

### Stable error envelope and codes

Errors use:

```json
{
  "error": "human-readable message",
  "code": "stable_machine_code",
  "request_id": "optional",
  "broker_revision": 184,
  "details": {}
}
```

Initial stable codes are:

| Code | Normal HTTP meaning |
| --- | --- |
| `development_broker_unavailable`, `runtime_unavailable`, `runtime_peer_unavailable` | `503` |
| `runtime_peer_protocol_error` | `502` |
| `unsupported_api_revision`, `capability_unavailable` | `409` |
| `source_registration_required`, `source_drift` | `409` |
| `request_conflict`, `idempotency_conflict` | `409` |
| `operator_pause`, `initialization_in_progress`, `cleanup_required` | `423` |
| `runtime_not_yielded`, `screen_not_authoritative` | `412` |
| `lease_not_active`, `lease_binding_mismatch` | `409` |
| `lease_operation_limit` | `409` |
| `request_expired`, `lease_heartbeat_lost` | `410` |
| `target_generation_changed`, `battle_generation_changed`, `frame_generation_changed` | `409` |
| `frame_stale`, `input_precondition_failed` | `412` |
| `action_catalog_changed` | `409` |
| `action_not_catalogued` | `403` |
| `action_parameters_invalid` | `422` |
| `input_not_allowlisted`, `owned_validation_authorization_required` | `403` |
| `direct_read_not_allowlisted`, `direct_capture_not_justified` | `403` |
| `rate_limited` | `429` with `retry_after_seconds` |
| `runtime_backpressure` | `503` with `retry_after_seconds` |
| `audit_unavailable`, `dispatch_outcome_unknown` | `503` |

Clients branch on codes, not message text. A new code is additive; changing the
meaning of an existing code requires a protocol revision.

### CLI

A checked-in `tools/development_access.py` will be the supported client. It
connects only to `development-broker.sock`, never `runtime-peer.sock`, and
offers machine-readable JSON for:

```text
status
source register
frames current
capture
request
watch
heartbeat
resume
input
complete
cancel
```

`watch` owns the short-lived token file and heartbeat until completion,
revocation, or interruption, then requests safe cancellation. The CLI refuses
to run a state-changing subcommand from the production checkout, prints the
registered source fingerprint before requesting authority, never manages ADB
connections, and has no raw shell/input escape hatch.

## Durable audit contract

The broker serializes audit events with a monotonic event sequence and
hash-chains complete JSONL records. Every request transition, yield
acknowledgement, grant, renewal, direct read/capture, runtime-peer
authentication/registration/rejection, negotiation or sequence failure,
backpressure/channel shutdown, input intent/result, revocation, cleanup result,
compatibility failure, source drift, restart reconciliation, and operator
queue change is recorded.

Each applicable event contains:

- schema version, event ID/sequence, prior-record hash, timestamp, event type,
  disposition, and stable reason/error code;
- service epoch and broker revision;
- Unix principal and bounded client identity;
- request/client/idempotency IDs and capability;
- source registration ID, canonical worktree path, branch, HEAD, dirty flag,
  and source fingerprint;
- runtime session/PID/action sequence;
- ADB target and target generation;
- battle generation/identity and detected state;
- frame-source generation and exact frame sequence;
- lease ID/generation and deadlines, never token material or token hash;
- runtime-peer connection ID plus broker/runtime sequences for peer-channel
  events;
- action-catalog revision/digest, stable action ID, sanitized bounded
  parameters, operation ID, and dispatch/postcondition outcome; and
- cleanup classification and unresolved-operator reason.

State-changing intent must be appended and synced before dispatch. Terminal
request and cleanup events are synced before the queue advances. Passive
published-frame reads are not individually audited; frame publication,
invalidation, direct capture, and retention anomalies are.

## Regression and validation matrix

Implementation is not complete until the relevant phase proves these seams:

| Layer | Required regression |
| --- | --- |
| Source/unit | Clean, staged, unstaged, untracked, symlink, submodule, special-file, concurrent-change, detached-HEAD, and ignored-file fingerprint cases |
| Environment/unit | Dependency fingerprint determinism, serialized two-builder race, failed-stage cleanup, immutable reuse, wrong-interpreter rejection, and proof production `.venv` is never resolved |
| Frame/unit | Bundle metadata/hash validation, incomplete/black frame rejection, sequence/source changes, atomic pointer replacement, crash at every sync/rename boundary, no-follow behavior, and bounded retention |
| Fake clock | Queue wait, acknowledgement, heartbeat, rolling lease, hard deadline, suspended-request heartbeat, rate limit, and coalescing boundaries |
| Fake runtime | Runtime-initiated registration, yield only after the App serializer reports zero in-flight input, socket receiver never dispatching input, existing production hold precedence, no generic owner authorization for the external hold, cleanup classification, and exact status ownership |
| Runtime peer authentication | `SO_PEERCRED` UID/PID checks, systemd MainPID mismatch, stale/reused runtime session, wrong service epoch, missing/mismatched held target lock, target-generation change, second peer rejection, external-client protocol rejection, and proof a worker PID cannot register as the runtime |
| Runtime peer ordering | Length/UTF-8/JSON limits, partial-frame deadline, negotiation failure, strict per-direction sequences/acknowledgements, duplicate same-digest replay, changed-payload duplicate rejection, gaps/out-of-order frames, revocation barriers, and orderly shutdown |
| Runtime peer disconnect/replay | Broker restart, runtime restart, same-epoch reconnect, channel loss during each message, response loss before/after possible input, cached result reconciliation, unknown-outcome cleanup, and proof no operation dispatches twice |
| Runtime peer backpressure | Broker/runtime byte and count limits, one input in flight, status coalescing, reserved revocation lane, full-queue rejection, heartbeat expiry, App wakeup, and fail-closed control-lane exhaustion |
| Fake ADB | Exact-target argv, pre/post target-generation checks, direct-read coalescing, timeout, malformed capture, disconnect, handoff, and absolute rejection of connection/input commands |
| API | Revision/capability gating, Unix-only mutation routes, schemas, action-catalog resource, unknown fields/enums, error codes, optimistic revisions, idempotent request/input replay, and token redaction |
| Concurrency | Many passive readers, capture coalescing, FIFO eligible scheduling, one global interactive lease, competing worktrees, Pause racing activation/input, and Game Over racing heartbeat |
| Action catalog | Canonical digest/revision, stable ID compatibility, capability/state/guard/parameter/postcondition checks at request and dispatch, disabled/allowlisted rollout, production dependency digest drift, catalog change during a lease, unknown action rejection, and proof no worker path/asset/callback is opened by the input path |
| Authority | Published frames confer no input; external hold is distinct from Pause, `AuxiliaryRouteLease`, and exclusive-validation receipts; every gateway input rechecks all bindings and resolves only a production-catalog action |
| Source drift | Drift while requested, queued, acknowledged, active, and suspended; no input after drift and no inheritance after re-registration |
| Crash/restart | Broker, runtime, client, publisher, and target-owner restart at each request state and before/after possible input; no old token or receipt replay |
| Retained frame | Detection/analysis from immutable fixture and atomic bundle without ADB, including battle and frame-source boundary resets |
| Pause precedence | Pause before request, during yield, immediately before gateway dispatch, after possible input, during cleanup, and while suspended; no development input while Pause/Stop is authoritative |
| Terminal/Home | Natural Game Over revocation, production terminal handling, eligible Home request ordering, no Home request path, full initialization/preflight, fresh new token only at stable next-running readiness, and Home cleanup revalidation |
| Owned validation | Separate authorization/receipt, atomic claim before ordinary New Battle, Tournament exclusion, exact owner/target/battle binding, permitted and prohibited Surrender cleanup, and orphan failure |
| Failure cleanup | Unexpected menu, ambiguous screen, lost result, audit failure, postcondition failure, operator-required state, and queue blockage until safe resolution |
| Git integration/promotion | Worker commits remain feature-only; master cherry-pick and develop-only conflict resolution; clean tracked/index and resolved-nonignored-file gates in both checkouts; exact reviewed range; main-ancestor check; fast-forward-only exact commit while production stays on main; parallel main change blocking only promotion; emergency-hotfix back-integration; and evidence-retained cleanup |
| Separately authorized live | One master-scheduled target: verify passive publication first, then coordinated capture, Pause race, bounded running lease, natural boundary continuation, Home request, and—only with explicit authority—one owned validation battle and cleanup |

Ordinary CI and worker validation must use fakes and retained frames. Live
validation is a distinct, master-scheduled authorization and must follow the
then-current runbook and repository safety rules; this document does not itself
authorize a device action.

## Resolved design recommendations

The architecture intentionally resolves the design questions as follows:

- Extend the existing production control-surface service; do not add a third
  daemon.
- Connect the authenticated production runtime to that broker through the
  single persistent runtime-peer Unix-socket protocol; do not let an external
  client or socket thread dispatch input.
- Represent development yield as a distinct suppressive external authority
  hold; never overwrite or borrow operator `PAUSED`.
- Prefer published frames, permit audited exact-target passive reads and a
  narrowly justified one-shot direct capture, prohibit development connection
  management, and route every mutation through the production gateway.
- Keep service, runtime, target, battle, frame source/sequence, lease, and
  development source identities separate.
- Keep volatile broker material in `$XDG_RUNTIME_DIR` and durable audit under
  `$XDG_STATE_HOME`, never in a worktree-local lock tree.
- Resolve live actions only from the production-installed, runtime-published
  action catalog; source registration never supplies executable action assets.
- Bootstrap reproducible content-addressed development environments shared
  read-only by workers; never share production's mutable `.venv`.

These choices preserve the production runtime's current ownership while making
future development access explicit, bounded, observable, and revocable.
