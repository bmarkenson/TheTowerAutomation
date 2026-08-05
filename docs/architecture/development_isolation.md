# Production and Development Coordination

This document is the canonical design for TheTower's production checkout,
development worktrees, shared screenshots, and coordinated use of the one
emulator. It replaces the archived
[high-assurance design](history/development_isolation_high_assurance_2026-08-03.md),
which assumed a threat model this project does not have.
Delivery is tracked in the
[tooling and maintenance backlog](../backlog/tooling-and-maintenance.md#production-and-development-coordination-delivery).

## Trust model and design standard

TheTower is a single-user hobby automation project. The operator and Codex
worker threads are trusted, cooperative participants running under the same
Unix account. There is no requirement to protect screenshots, logs, source,
tokens, or emulator state from another same-user process. A deliberately
hostile same-user process could bypass application-level controls anyway; real
protection from that actor would require separate operating-system identities
or containers, which are intentionally out of scope.

The design therefore protects against ordinary engineering failures:

- two cooperative threads accidentally trying to control the emulator at once;
- development changing the production checkout or Python environment;
- production acting while a worker believes it has an interactive window;
- a stale worker acting after a runtime, target, battle, or lease boundary;
- readers seeing a partially written screenshot;
- an abandoned lease leaving production indefinitely held; and
- an uncertain input being repeated automatically.

Normal file permissions, request identifiers, checksums, and locks may still be
used where they are the simplest correctness mechanism. They are not security
boundaries. Do not add source attestation, secret capabilities, peer
authentication, cryptographic audit, hostile-filesystem defenses, or custom
secure transport unless the operator explicitly changes this threat model.

## Outcomes

The coordination work has four practical outcomes:

1. production stays at its established path and remains the only long-lived
   automation/runtime owner;
2. feature worktrees can edit and test concurrently without using production's
   Python environment;
3. workers can freely use retained fixtures, production screenshots, or
   bounded read-only ADB capture when those are useful; and
4. emulator input is serialized through one cooperative, expiring development
   lease that production explicitly acknowledges.

The project should implement the smallest mechanism that satisfies those
outcomes. New protocol layers, identity namespaces, queues, state machines, or
retention systems require a demonstrated need rather than speculative future
use.

## Repository and Git topology

The repository uses linked Git worktrees, not separate repositories:

```text
/home/brianm/dev/python/TheTower
    main                      production checkout and systemd working path

/home/brianm/dev/python/TheTower-worktrees/dev
    develop                   integration checkout

/home/brianm/dev/python/TheTower-worktrees/workers/<task>
    feature/<task>            one bounded worker checkout per task
```

`main` is production, `develop` is integration, and feature branches are
temporary. Workers commit only their owned feature changes. The master reviews
and integrates them into `develop`, runs the combined gate there, and promotes
an exact validated `develop` commit to `main` only by fast-forward.

Production is never switched to `develop` or a feature branch. Existing
operator or parallel changes are preserved. A non-clean production checkout
blocks promotion but does not block unrelated feature or integration work.
There is no need to fingerprint or attest a worker's complete source tree for
emulator access. Branch, HEAD, and an ordinary dirty summary are sufficient
diagnostic context in a handoff or lease log.

### Staging, promotion, and rollback

`develop` is the project's only standing staging layer. It provides a clean
integration point, its own Python environment, the complete non-live
checkpoint, retained-frame testing, and a place to identify one exact release
candidate. A permanent `staging` branch, another long-lived checkout, or a
second staging runtime would not provide meaningful live isolation: there is
only one emulator, so a second runtime would still have to displace production
to test it. A temporary clean checkout may be created for a specific
reproducibility test, but it is not another promotion stage and receives no
special emulator authority.

Feature and integration work never modify the production checkout, production
`.venv`, or installed services. The fixed systemd units therefore continue to
stop and restart the currently deployed `main` revision throughout ordinary
development.

The normal release path is deliberately direct:

1. integrate reviewed feature commits into `develop` and run the combined
   non-live gate there;
2. record the exact current production commit `M` and candidate commit `D`,
   require `M` to be an ancestor of `D`, and require the production checkout to
   have no unresolved local work;
3. create a uniquely named pre-deployment tag at `M` so the prior source is
   easy to identify;
4. stop each affected long-lived service before changing any file it may import
   or read, then fast-forward the production checkout—while it remains on
   `main`—to exact commit `D`;
5. update production dependencies only when their tracked inputs changed,
   restart each affected service, and perform a bounded production smoke test;
   and
6. if the smoke test fails, stop the affected service, create a normal rollback
   commit that reverses the reviewed `M..D` range, restore any separately
   changed environment or installed unit, restart, and bring the rollback back
   into `develop` before another promotion.

Documentation-only promotion does not require a runtime stop. Dependency,
persistent-state format, and installed-systemd-unit changes require an explicit
rollback plan for those non-Git effects; ordinary code changes do not acquire
extra ceremony merely because they will be tested in production. Rewriting
`main` backward is not the normal rollback mechanism, because a revert preserves
what was deployed and why.

The executable checklist is in
[runtime operations](../runtime_operations.md#production-promotion-and-rollback).
Add a separate release/staging layer only after repeated direct-promotion
failures demonstrate a concrete capability it would provide.

## Development Python environment

Production's `.venv` remains production-owned. Development worktrees never
execute, copy, link, install into, or mutate it, and the production systemd
units never use a development environment.

### Retained Phase-0 results

The Phase-0 prototype on `develop` established several worthwhile contracts:

- exact interpreter and direct dependency declarations are tracked;
- runtime and development dependency closures are pinned;
- a worktree selects a development environment through its ignored `.venv`;
- concurrent builders are serialized; and
- one non-live checkpoint runs compilation, maintained static validators, and
  pytest without starting the runtime or accessing ADB.

Those outcomes remain. The prototype's immutable manifest, whole-tree
permission changes, staging-prefix relocation, installed `RECORD` rewriting,
no-follow adversarial checks, host-tool blockers, and special Tesseract skip
policy were not required by the clarified trust model and have been removed.

### Current compact bootstrap

The bootstrap remains content-selected so two branches with different
dependency inputs do not silently share the wrong packages. Its complete
publication path is deliberately small:

1. hash the tracked interpreter and dependency inputs;
2. choose a sibling development-environment path from that digest;
3. acquire one ordinary writer lock;
4. create the virtual environment directly at its final path and install the
   pinned dependencies;
5. run `pip check` and write a small completion marker containing the input
   digest; and
6. point the worktree's ignored `.venv` symlink at the completed environment.

If a build is interrupted before the completion marker, the next serialized
builder may remove and rebuild only that exact validated child of the
development-environment store. A completed valid environment is reused. A
completed environment that fails validation is reported clearly and is never
automatically removed or repaired while another worktree may be using it.
There is no requirement to make the environment immutable, inventory every
installed byte, defend against a malicious same-user path replacement, or
relocate a completed virtual environment. Ad-hoc `pip install` remains
unsupported because it causes accidental dependency drift, not because the
environment is a security asset.

The checkpoint isolates ordinary generated test output and runs the full
repository-local suite. Installed host tools such as Tesseract may run in
non-live tests. ADB-facing tests use fakes or mocks unless a thread has
completed the live-runtime startup path and deliberately requested live
validation. The supported entrypoint and operator workflow are documented in
`docs/new_thread.md`.

## Screenshots, fixtures, and read-only ADB

Production screenshots and other runtime-generated images are not sensitive.
Workers may read or copy them for debugging, retained regression fixtures, or
test development. A copied durable fixture follows the normal repository
fixture and documentation rules, but no privacy or security approval is
required.

The preferred evidence source is whichever is simplest and sufficiently
current:

1. a tracked fixture for deterministic regression work;
2. an existing production screenshot or shared latest frame;
3. a fresh bounded exact-target screenshot capture when current device state
   materially matters.

Reading or copying an existing production artifact as historical test evidence
does not require a lease or live inspection. It also does not prove current
runtime state. Using an artifact as current evidence or invoking ADB requires
the live startup/inspection path in `docs/new_thread.md`, because those facts
are volatile and concurrent production activity matters. That requirement is
operational, not a confidentiality boundary.

Bounded exact-target read operations such as `get-state`, screenshots, and
non-mutating shell/file reads do not require an interactive lease. Workers may
perform them directly after the required live inspection. Production remains
responsible for ADB connection management and long-lived capture processes;
workers do not run `adb connect`, start/kill the ADB server, or start a second
continuous screenrecord pipeline merely to answer a read-only question.

### Shared latest frame

Production's stable reader paths are
`/home/brianm/dev/python/TheTower/screenshots/latest.png` and
`/home/brianm/dev/python/TheTower/screenshots/latest.json`. The writer remains
checkout-relative: the existing default capture path resolves there because
production runs with `/home/brianm/dev/python/TheTower` as its working
directory.

The normal capture/save boundary publishes only a successfully decoded,
complete, normalized frame. It encodes the complete PNG in memory, writes a
task-owned sibling temporary file, and calls `os.replace`, so a concurrent
reader sees either the previous complete PNG or the new complete PNG. Custom
output paths use the same atomic PNG writer, while only the canonical latest
path gets the sidecar. Capture, encoding, temporary-write, and replacement
failures cannot truncate or remove the previously published PNG, and owned
temporary files are removed after success or handled failure.

Schema 1 of `latest.json` contains exactly these observation fields:

```json
{
  "schema_version": 1,
  "captured_at": "2026-08-04T18:19:20.123456Z",
  "adb_target": "localhost:5555",
  "native_width": 720,
  "native_height": 1280,
  "canonical_width": 1080,
  "canonical_height": 1920
}
```

`captured_at` is an RFC 3339 UTC timestamp and `adb_target` is the exact target
resolved for that capture. The JSON is atomically replaced as its own file
after the PNG. It may therefore briefly lag the PNG and is neither a
transactional frame bundle nor input authority. Metadata failure leaves the
valid in-memory observation and atomically published PNG usable; capture
failure changes neither published artifact.

The shared files are test and observation data. Existing files may be read or
copied without a lease or live inspection, but they do not prove current
runtime state. Treating them as current-state evidence still requires the live
startup inspection, and the sidecar grants no input authority. The feature
does not need immutable bundle directories, cryptographic identity chains,
source or battle generations, a broker receipt, history/retention machinery,
or an interactive lease. Workers may copy a useful frame into their own task
output or a reviewed tracked fixture.

## Interactive emulator coordination

Read-only observation is concurrent. Device input is exclusive.

The existing production control-surface service is the natural coordinator
because it already observes runtime/process evidence and writes control
directives. The implemented lease extends that local JSON/HTTP model and the
runtime-owned structured authority snapshot; it adds no third daemon, second
authenticated Unix protocol, custom persistent peer channel, or worktree lock.

### Minimal lease

There is at most one live interactive development request. The control file
retains its requested state separately from the runtime acknowledgement. The
minimal record contains:

- a lease ID used as a coordination handle, not a secret;
- an owner/task label for operator comprehension;
- request, acknowledgement, heartbeat, expiry, release, and terminal times as
  applicable;
- the exact ADB target;
- the production runtime PID/session or equivalent fresh process evidence;
- the production acknowledgement that its development hold is installed; and
- a concise starting screen/battle description when known; and
- a terminal disposition and reason.

`POST /api/v1/interactive-development-lease` accepts the three small operations
`request`, `heartbeat`, and `release`. A request supplies only a bounded
`owner_label`; heartbeat and release supply the ordinary `lease_id`. The
control surface binds a request to the fresh runtime-owned session ID, PID, ADB
target, screen, and battle evidence it has already verified against the held
runtime lock. A conflicting live request returns `busy`/HTTP 409. The fixed
30-second heartbeat expiry is server policy rather than client negotiation.

There is no source registration, complete worktree fingerprint, secret bearer
token, client authentication handshake, service epoch, capability negotiation,
or cryptographic binding tuple. A fresh request after a boundary is cheaper and
clearer than preserving a complex suspended request.

### Production acknowledgement and hold

An interactive lease becomes active only after the production runtime:

1. observes the request and confirms operator control is not `PAUSED` or
   `STOPPED`;
2. lets any already-started input finish;
3. installs a distinct `external_development` authority hold that blocks every
   normal in-process strategy, handler, auxiliary, recovery, initialization,
   lifecycle, and blind-tapper input; and
4. publishes an acknowledgement and a fresh observation.

The main loop installs the hold only between runtime input workflows. It then
cooperatively stops the floating-gem tapper and withholds acknowledgement until
that worker is inactive and a known fresh screen has been detected. The
runtime acknowledgement is carried by the same atomic
`logs/strategy_action_gate.json` channel as the typed authority matrix. Status
reports a lease active only when that snapshot is fresh, its runtime/PID/target
matches both the request and active lock, the `external_development` hold is
present, observation remains allowed, and every input authority class is
denied.

The development hold is not operator Pause and must not overwrite
`logs/automation_ctl.json`. Capture and detection may continue while it is
installed. Existing `AuxiliaryRouteLease` and exclusive-validation receipts do
not satisfy a development lease. The hold grants no generic in-process owner
route; it is suppressive because development input occurs outside the normal
runtime producers.

Operator `PAUSED` or `STOPPED` always wins. It ends development input and does
not automatically reactivate a lease on Resume.

### Development input

Once production has acknowledged the lease, the worker may send bounded
exact-target ADB input through a small lease-aware helper. The helper rechecks
the current active lease, production acknowledgement, target, and expiry; logs
the intended input; invokes one command; and records the result. Its purpose is
to prevent cooperative threads from accidentally acting outside their window.
It is not intended to resist deliberate bypass by the same Unix user.

The first implementation may support ordinary taps, swipes, and the existing
project screenshot/read helpers. It does not need a production-published
semantic action catalog, dependency digests, per-action capability tokens,
idempotent replay protocol, or a runtime mailbox. Higher-level development
code may still use existing project detectors and clickmap coordinates before
calling the helper.

An input whose result is uncertain is never repeated automatically. The worker
captures a fresh screen and decides from current evidence or asks the operator.

That helper is delivery step 4 and is not implemented by the current lease/hold
delivery. An acknowledged lease is therefore structured coordination state for
the later helper; it does not by itself make ad-hoc raw ADB input a supported
project workflow.

### Release and boundaries

On normal completion, the worker stops input, captures a fresh screen, and
releases the lease. Production rechecks current state before removing the
development hold and resuming its own actions. If the screen is unexpected or
ambiguous, the safe response is to retain the no-input hold or apply an
operator-owned Pause and report the condition; no elaborate automatic cleanup
state machine is required initially.

The implemented runtime keeps a release request suppressive until a subsequent
known observation. `UNKNOWN` or failed terminal persistence remains visible and
keeps the hold. Pause/Stop revokes immediately because operator control is the
stronger authority. Expiry waits for a fresh known frame before production
input resumes, while the expired deadline already prevents the request from
remaining active.

Heartbeat loss, runtime replacement, ADB target change, and an authoritative
battle boundary end the lease. At natural Game Over, production regains normal
terminal authority and the worker requests another lease later if needed.
There is no automatic suspension, Home queue, or next-battle token renewal.

A Home-boundary test is simply a lease requested when fresh evidence shows
Home. Starting or surrendering a deliberately owned validation battle remains
subject to the explicit authorization rules in `AGENTS.md`; it should be added
only when a concrete test needs it, not as part of the first lease delivery.

## Logging and recovery

Use the existing action log. Record lease request, production acknowledgement,
activation, heartbeat expiry, input intent/result, release, and abnormal
termination in operator-readable form. A separate hash-chained audit ledger,
long retention policy, token redaction system, and replay cache are unnecessary.

After a coordinator or runtime restart, any old lease is inactive. Fresh
process, target, control, and screen inspection determines whether production
can resume. The system does not attempt to prove continuity across a crash.

## Reassessment of the accepted prototype

The earlier work is modified forward rather than erased or history-rewritten:

| Area | Keep | Simplify, remove, or defer |
| --- | --- | --- |
| Git topology | `main`, `develop`, feature worktrees, master integration and fast-forward promotion | No independent repository per worker; no source attestation |
| Python isolation | Separate production environment, tracked pins, content-selected development environments, one builder lock, checkpoint | Compact completion-marker bootstrap; immutable manifests, relocation, no-follow hardening, whole-tree fsync/permissions, and host-tool blocking removed |
| Screenshots | Complete-frame validation and atomic latest replacement | No confidential-data treatment, immutable bundle hierarchy, hash identity chain, or broker receipt |
| Read-only ADB | Bounded exact-target reads after live inspection; production owns connection management | No lease or source registration for reads/capture |
| Interactive coordination | One production-acknowledged exclusive lease, distinct hold, heartbeat/expiry, exact target, fresh release check | No secret tokens, peer authentication, source fingerprint, capability negotiation, fairness queue, or automatic continuation |
| Input | Central lease-aware helper, intent/result logging, no automatic retry of uncertainty | No production semantic action catalog, custom runtime mailbox, ordered replay protocol, or per-action cryptographic bindings |
| Lifecycle | Pause precedence, lease ends on runtime/target/battle boundary, production owns Game Over | Defer Home scheduling, suspended requests, next-battle renewal, and owned-validation automation until an actual test requires them |
| Audit | Existing human-readable action log | No separate hash chain, secrecy, or long-lived security ledger |

The archived design remains available for provenance, but none of its removed
mechanisms should be implemented merely because it was previously documented.

## Delivery order

Implementation proceeds in small reviewable steps:

1. **Completed: correct the contract and simplify Phase 0.** The prototype
   runner has been replaced by the compact bootstrap/checkpoint described above
   and the full non-live suite includes normal host-backed OCR tests.
2. **Completed: make screenshots convenient.** The existing runtime capture
   path atomically publishes the latest complete canonical PNG and its small
   advisory capture/target/geometry sidecar. Direct bounded read-only capture
   is documented separately from interactive input authority.
3. **Completed: add the suppressive hold and minimal lease.** The existing
   control-surface/directive and runtime-owned authority paths now provide one
   request/acknowledgement/heartbeat/release lifecycle and prove production
   input quiescence before acknowledgement.
4. **Add the lease-aware ADB input helper.** Support one bounded command at a
   time, exact-target checking, readable logging, expiry, and release.
5. **Perform bounded live validation.** The master schedules one cooperative
   lease, verifies Pause precedence and expiry, and confirms clean return to
   production. Add Home or owned-battle behavior only in response to a concrete
   development need.

Do not implement a later step merely to make the current step look complete.
Repository-local tests use fakes and retained or copied production frames;
live validation remains a separately inspected master action.

## Regression expectations

The useful regression seams are correspondingly small:

- development never executes, copies, links to, installs into, or mutates
  production's `.venv`;
- dependency-input changes select the correct development environment;
- concurrent bootstrap attempts serialize and a marker-absent interrupted
  build recovers;
- completed valid environments are reused while completed invalid environments
  are reported without automatic mutation;
- worktree selection is atomic and status rejects missing, mismatched,
  incomplete, or broken selections;
- lock verification and regeneration remain deterministic;
- the normal checkpoint isolates generated state, runs the full
  repository-local test suite, and returns a failing command's status;
- a shared screenshot reader sees an old or new complete image, never a partial
  write, and failed publication preserves the prior image and cleans owned
  temporary files;
- the advisory sidecar is valid atomically replaced schema-1 JSON with capture,
  target, and native/canonical geometry, while sidecar failure does not discard
  the usable frame or corrupt the published PNG;
- read-only ADB commands are exact-target and bounded;
- only one interactive lease becomes active;
- production acknowledges its external hold before development input;
- Pause, Stop, heartbeat expiry, runtime replacement, target change, and battle
  boundary end input authority;
- the lease-aware helper rejects absent, stale, mismatched-target, or
  unacknowledged leases and logs every attempted input;
- uncertain input is not automatically repeated;
- release requires a fresh observation before production removes its hold; and
- the master integrates worker commits into `develop` and promotes only an
  exact clean validated fast-forward candidate.

These tests protect the project from realistic accidents without turning a
hobby automation repository into a same-user security system.
