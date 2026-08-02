# Control Surface Architecture

The primary control surface is a native Windows WPF application. A small
Linux-side service exposes the same repository-local controls and records used
by the automation and CLI. The earlier browser client remains a useful fallback
served by that same API.

```text
Native Windows WPF app
      ├── current-user named pipe ──► TheTower.TunnelHost.exe
      │                                  ├── API ssh.exe local forward
      │                                  ├── ADB ssh.exe reverse forward
      │                                  └── fixed SSH query/actions
      │                                      ► thetower-control-surface.service
      └── loopback HTTP through API forward
                         ▼
Linux loopback HTTP server
      ├── write ──► logs/automation_ctl.json ──► automation supervisor
      ├── write ──► ~/.config/thetower/automation-adb.env ──► next-start config
      ├── write ──► logs/host_performance.sqlite3
      ├── manage ► fixed thetower-automation.service
      ├── read  ──► logs/actions.log
      ├── read  ──► logs/activity_scope.json
      ├── read  ──► logs/automation-*.lock
      ├── read  ──► logs/battles/Battle*.json
      └── read  ──► logs/tournaments/Tournament*.json
```

The Windows app is a thin client. A self-contained `win-x64` package contains
the WPF GUI and its headless tunnel-host companion and needs no Python, ADB,
repository checkout, browser, or preinstalled .NET runtime on the operator PC.
The Linux adapter remains independently testable and transport
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
- Windows host-performance telemetry is observational. Publishing an aggregate
  cannot change automation intent, process state, ADB ownership, or device
  input authority.
- Battle type is evidence-based. A Tournament Results terminal identifies a
  Tournament; standard Game Over plus the shared Tournament/Milestone profile
  identifies a Milestone; Farm strategy/profile identity identifies Farm.
  Tournament settings alone never decide between Tournament and Milestone.
  Terminal-observed Tier is shown independently, including for an ambiguous
  standard Game Over whose type remains `unknown`.
- The allowlisted write surface is pause, timed pause, resume, mode,
  persistent numeric game-speed target selection,
  resolution of a runtime-published startup-gate decision,
  optional strategy-scoped one-run check configuration,
  bundled or validated custom-strategy selection, constrained custom Farm
  profile publication, stopped or
  acknowledged-paused ADB-port
  configuration, fixed managed-service start/stop, and one guarded active-
  battle automation reload. Active strategy
  requests are declarative runtime configuration, not direct tap authority.
  Profile publication writes only a fixed-name file beneath
  `config/strategies/custom`; it does not select, queue, adopt, start, restart,
  stop, pause, resume, or otherwise apply that profile.
  There is no arbitrary tap, shell command, process kill, direct Surrender,
  file-path, or ADB endpoint.
- Complete stop persists `STOPPED` before asking the fixed systemd user service
  to stop. Start always crosses the service boundary under `PAUSED`; a requested
  `RUNNING` directive is saved only after systemd reports the unit active.
- Guarded active-battle reload never persists ordinary `STOPPED`. It refreshes
  same-state Pause intent so the runtime acknowledges the request and forces a
  new detection/status sample, requires fresh `RUNNING` evidence from the
  matching MainPID/ADB-lock owner, and then replaces only the fixed automation
  unit. The replacement must prove a distinct PID, refreshed lock ownership,
  one-launch `next_run` policy, Pause consumption, and a first observation
  before the prior control state is restored. Failure after preparation begins
  remains paused; an initial precondition rejection does not mutate control.
- A stopped start request automatically distinguishes verified Home
  `NEW_BATTLE` from an already-active/resumable battle. Home always runs the
  complete pre-battle gates. An existing battle is attached without inventing a
  run boundary. `auto_validate` performs one read-only strategy validation; a
  Home-repairable mismatch offers guarded restart/repair as an explicit
  operator decision. If Battle History proves the Current-run scope still
  identifies the same battle and that scope holds a matching completed-check
  receipt, the attached session checks are reused instead of repeated.
  Missing, stale, unreadable, or configuration-mismatched evidence retains the
  declared attachment validation. `auto` skips all strategy setup checks for
  only that attached battle. A terminal result or verified Home `NEW_BATTLE`
  clears the attachment choice, so the next battle runs its real gates without
  fabricated completion state. `next_run` remains the guarded-reload policy
  and `immediate` is the explicit forced-first-battle policy.
- An active strategy request persists the next-start setting and a versioned
  control directive. By default it remains pending during a battle. The
  current strategy first finalizes the terminal report and its Game Over hook;
  the pending strategy is then installed before Retry/Home navigation or
  before the next run's first actionable observation. Verified Home
  `NEW_BATTLE` and Workshop are authoritative no-battle boundaries, including
  while paused. Home `RESUME_BATTLE` never authorizes a boundary switch.
  Selecting the current strategy replaces and thereby cancels a different
  pending request.
- Every explicit Tournament selection, including a stopped process Start with
  Tournament selected, also creates a one-use exclusive-validation receipt
  bound to that strategy request and generated-plan fingerprint. Status exposes
  its pending, owned, cleanup, or result disposition. The request authorizes
  only the runtime's profile-declared ordinary `NEW_BATTLE` validation. A ready
  receipt exposes one narrow operator-confirmed Tournament-launch decision,
  which the matching live runtime must claim before its first input. It is not
  arbitrary tap authority and never grants Surrender authority. A replacement
  process reports an active validation or launch receipt owned by the former
  runtime as failed and cannot replay, continue, clean up, or Surrender it.
- An explicit `apply_to_active_run` strategy request may instead be adopted
  after fresh `RUNNING` or Home `RESUME_BATTLE` evidence. Adoption changes
  normal strategy behavior and the strategy/profile identity used by Battle
  End reporting, but uses attachment semantics: run initialization, session
  preflight, and Home-only gates remain deferred until the next genuine
  new-run boundary, except for an explicitly declared read-only observer check.
  If `NEW_BATTLE` is observed first, the request follows the normal
  boundary-install path and all new-run gates remain active.
- The API never accepts a PID, executable, service name, or command from the
  Windows client. The Linux server is configured with one validated unit name.
- A malformed control file is reported and preserved rather than overwritten.
- Status advertises an API version, a monotonic server revision, and explicit
  capabilities. The Windows client evaluates all three: it requires the
  expected API version, a compiled minimum server revision, and its required
  capabilities. A feature that makes the current Windows client depend on new
  Linux behavior must advance the Linux server revision and the client's
  minimum revision in the same change; independently gated features should
  also advertise and require a named capability.
- Connecting the Windows client remains read-only. An incompatible API,
  insufficient server revision, or missing required capability disables the
  dependent action and shows one generic client/server compatibility warning.
  Independently of HTTP, the client may use its validated SSH destination to
  query and start, stop, or restart only the fixed
  `thetower-control-surface.service` user unit. Stop and restart require
  confirmation; start and restart must reconnect and verify the complete
  compatibility contract. Restart reloads the installed Linux code but does
  not deploy an update. This path cannot select another unit or command and
  never restarts main automation.

Control writers use a companion advisory lock and atomic replacement. Timed
pause expiry revalidates its exact deadline while holding that writer lock, so
an operator extension or replacement with an indefinite pause wins over a stale
expiry attempt.

## Transport and access

The server binds to `127.0.0.1:8787` by default. A dedicated per-user
`TheTower.TunnelHost.exe` owns two passwordless OpenSSH processes: the
Windows-local API forward and a separately controllable ADB reverse forward.
The WPF app never starts or adopts `ssh.exe`; it starts or attaches to the host
and uses its local IPC protocol. The host invokes `ssh.exe` without a shell,
uses only validated destination/port fields, and enables BatchMode, strict
host-key checking, a bounded connect timeout, forward-failure detection, and
keepalives. The equivalent manual commands are:

```powershell
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
ssh -N -R 127.0.0.1:5555:127.0.0.1:5555 <linux-user>@<linux-host>
```

The reverse listener address is fixed to Linux loopback. Its Linux port and
Windows BlueStacks port are separate settings, both defaulting to 5555, so
multiple PCs can expose distinct Linux ports without changing their local
listeners. The independent process boundary keeps an ADB bind conflict or
reconnect cycle from interrupting API control. Accepted forwarding, local
Windows-listener detection, conflicts, and bounded automatic reconnect are
reported separately in the GUI. Both supervisors preserve desired state while
distinguishing starting, active, retry-waiting, conflict, faulted, and stopped
observation. A forwarding bind or policy conflict pauses retry only for that
tunnel; ordinary unexpected exits use independent 5/10/20/30-second capped
backoff.

The always-visible UI treats systemd API-service state, HTTP reachability, API
forward state, and ADB-forward state as four different signals. A fixed
`systemctl --user show` query supplies API-service state even while that service
is stopped. Service control uses bounded one-shot SSH commands; forwarding
continues to use the two independently owned long-running processes. The host
accepts only the fixed action enum and the validated destination; there is no
IPC field for a remote command, unit, path, shell, or executable.

### Persistent per-user tunnel host

The headless host is started on demand by the GUI and remains the authoritative
owner after the WPF process exits. It is neither a Windows service nor a tray
application and is not registered for login startup. Closing the GUI only
disconnects one IPC client. The host stays alive while either tunnel remains
desired; with no desired tunnel and no GUI connection, it exits after a
15-second bounded idle period. Because it remains an ordinary process in the
interactive user's logon session, Windows logoff ends it.

The IPC contract uses four-byte little-endian length framing around bounded JSON
messages. Every request carries protocol version 1, a bounded client identity,
a unique request ID, and one typed command. The stable named-pipe and mutex
identities are derived from the Windows user's SID. The server uses
`PipeOptions.CurrentUserOnly`; the pipe name deliberately does not contain the
protocol version so incompatible peers can return an explicit supported-version,
host-PID, instance, start-time, and executable-path response. The GUI disables
dependent commands on mismatch. Explicit host replacement either asks a
compatible host to shut down or verifies all of that incompatible-host process
identity before termination. Replacement stops owned tunnels and never replays
their desired state.

The snapshot returned on attach includes per-tunnel desired/observed state,
child PID, active endpoint, retry attempt/time, conflict/failure classification,
raw bounded SSH diagnostic, fixed Linux API-service observation, host instance,
host PID, and state revision. Reopening the GUI therefore observes the existing
owner rather than inferring ownership from arbitrary processes. The host never
enumerates or adopts pre-existing `ssh.exe` instances.

Validated configuration is stored per user in
`%LOCALAPPDATA%\TheTower\tunnel-host.json`, including the SSH destination, API
ports, Windows BlueStacks port, and distinct Linux ADB port. Desired state is
intentionally absent. A fresh host loads configuration with both supervisors
stopped, so a new logon, crash replacement, upgrade, or idle restart cannot
silently re-establish a forward.

Before any SSH child is created, the host associates itself with a Windows Job
Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Normal child
inheritance places both long-running forwards and bounded service-command SSH
processes in the same job. Closing the final job handle on graceful exit, crash,
forced termination, or logoff terminates every owned child without granting
authority over unrelated SSH processes.

A Windows service remains inappropriate because OpenSSH keys and `known_hosts`
are already scoped to the interactive user's profile. Optional start-at-login
and tray UI remain deferred choices rather than implicit consequences of this
ownership change.

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
| `GET` | `/api/v1/status` | Server revision/capabilities, control intent, acknowledgement, current-run identity, latest observation, structured Strategy Action Gate, and runtime evidence |
| `POST` | `/api/v1/control` | Allowlisted control mutation |
| `POST` | `/api/v1/process` | Start/stop or guarded-reload the fixed systemd automation unit, select its startup-gate policy, save/queue/adopt a bundled or published custom strategy, or configure/safely hand off its ADB port |
| `POST` | `/api/v1/host-performance` | Bounded, idempotent batches of native Windows host/BlueStacks performance aggregates |
| `GET` | `/api/v1/strategy-profiles` | Bundled/custom profile summaries plus the allowlisted Farm policy and preset catalogs |
| `POST` | `/api/v1/strategy-profiles` | Validate a constrained Farm draft or atomically publish its source and generated plan |
| `GET` | `/api/v1/strategy-authoring` | Registry metadata, separate Base/Strategy catalogs, editable source, effective resolution/provenance, compatible Base revisions, capabilities, and catalog errors |
| `GET` | `/api/v1/strategy-authoring/history` | Newest-first immutable custom-Strategy lineage and revision summaries, including retired lineages, without expanded plans |
| `GET` | `/api/v1/strategy-authoring/history/{id}` | One custom Strategy lineage and its ordered revision summaries |
| `GET` | `/api/v1/strategy-authoring/history/{id}/{version}` | One retained revision's review-safe source, Base snapshot, resolution, fingerprints, audit identity, and validation state without its generated plan |
| `POST` | `/api/v1/strategy-authoring` | Validate or publish Base/Strategy source, preview a Base pin, compare retained revisions, or review/confirm restore-as-new, without activation |
| `GET` | `/api/v1/battles?limit=N` | Newest Battle and Tournament summaries |
| `GET` | `/api/v1/battles/{battle_id}` | One full structured battle record |
| `GET` | `/api/v1/activity?limit=N&levels=ERROR,WARN&scope=current_run&after=CURSOR` | Recent structured action-log entries, optionally filtered by level, explicit run scope, and opaque clear-view cursor |

### Structured Strategy Action Gate status

`GET /api/v1/status` exposes `strategy_action_gate` separately from
`control.state`, state acknowledgement, and the latest observation. The object
reports availability, active/inactive and stale state, age, owner match,
strategy and battle scope, source/phase, failed check IDs, operator reason,
activation/update times, Pause/Stop context, active exclusive holds, the four
typed authority decisions, currently allowed auxiliary collectors, and any
exclusive auxiliary route.

The adapter reads only the runtime-owned atomic snapshot. It rejects malformed
or unsupported schemas, inactive publishers, expired timestamps, and PID/ADB
owners that do not match an active runtime lock. Missing or stale evidence is
reported as unavailable/stale and cannot be promoted into action authority.
Warning text in the action log is never parsed as gate state. Adding this
field is backward compatible: earlier clients ignore it and retain every older
endpoint and capability.

## Strategy profile publication

Server revision 17 advertises `strategy_profile_catalog_v1`. The native client
uses that catalog to populate strategy selection dynamically and to provide a
constrained Farm Profile Builder. Bundled profiles are immutable templates.
The editor can clone a Farm profile, choose its Tier, and assign `enforce`,
`observe`, or `preserve` to Modules, Damage Slider, Orb Distance, and Target
Priority. Preset choices come from the server's existing loadout catalogs; the
client never invents or submits a filesystem path, expanded rule, or executor
action.

Linux normalizes and validates the compact source through the same builder used
for checked-in profiles. A publication is one versioned YAML document containing
both that source and the exact generated plan, with independent SHA-256
fingerprints. The server re-generates the plan when reading a publication and
excludes a missing, malformed, tampered, or inconsistent file from selectable
strategy IDs. The latest facade is written through a same-directory staged
document, `fsync`, and atomic replacement while a companion advisory writer
lock serializes concurrent server requests. Every validated publication is
also retained as an immutable history revision through the recoverable two-
object transaction described below. Updating an existing custom profile still
requires the source fingerprint that was current when the editor loaded it, so
concurrent or stale edits fail with a conflict rather than overwriting a newer
revision.

Custom publications live under `config/strategies/custom` and are ignored by
Git as operator-owned configuration. Profile IDs are restricted to fixed-name
lowercase identifiers and cannot collide with bundled or legacy names. There is
no delete or arbitrary-path operation on the older profile endpoint. Selecting
or applying a published profile remains a separate explicit action through the
existing process API and its normal next-boundary or active-battle semantics.

## Sparse strategy authoring

Server revision 24 preserves `strategy_authoring_v1`,
`strategy_authoring_specialized_editors_v1`,
`strategy_authoring_profile_lifecycle_v1`, `strategy_action_gate_v1`, and every
older capability, retains `strategy_revision_history_v1`, and advertises
`strategy_authoring_local_loadout_editors_v1`. The additive
`/api/v1/strategy-authoring` endpoint implements the sparse Base/Strategy model
without changing `/api/v1/strategy-profiles`, `strategy_profile_catalog_v1`, or
`strategy_profile_editor_v2`. Pre-authoring native clients therefore keep using
their existing latest-only facade. The revision-23 authoring client retains its exact
preset-only metadata path against the newer service. The revision-24 client
requires the retained authoring, Strategy Gate, history, and local-loadout
capabilities and fails compatibility clearly against an older resident service.

The GET response carries the setting registry, normalized initial values,
behavior-free specialized-editor metadata, safe editor catalogs, separate Base
and Strategy collections, normalized source documents, effective resolution
and provenance, latest compatible Base revisions, structured capabilities, and
catalog errors. Metadata declares choices, object fields, list constraints,
dependencies, defaults, and toggle restrictions; Python normalizers and action
generation remain private. Modules, Target Priority, and Orb Distance retain
their top-level revision-23 `preset` editor contract and add a schema-versioned
`local_editor` object. Its server-validated fields and choices describe exact
Module slots/family candidates/uniqueness, complete Target membership/order,
and the three server-normalized Orb text fields. A revision-23 client ignores
that nested object; a local selector has no preset field for it to reinterpret.
Unsupported Strategy families remain listed with a read-only reason. Existing
schema-1 Farm publications are converted conservatively in memory and are not
rewritten merely because the catalog was opened.

The WPF client provides managed controls for all registered editor types:
fixed values, constrained booleans, server presets, server-normalized Damage
percentage text, Card recharge mappings, exact or variable lists, Perk bans and
order, Ultimate Weapon groups/toggles, and preset-or-local loadout definitions.
Module choices exclude a module already selected in another declared slot;
Target Priority exposes reorder only over the exact membership; Orb Distance
submits the three text fields unchanged for Linux normalization. Unknown
retained Ultimate Weapon groups and fields are merged back unchanged.
Source-state changes keep dormant values, preset/local changes keep both form
drafts, and validation-driven row reconstruction keeps those dormant values. A
server-supplied initial value is used when an omitted setting or form is first
materialized. The client does not implement a second normalizer or resolver.

POST accepts `validate_base`, `publish_base`, `validate_strategy`,
`publish_strategy`, `preview_rebase`, `retire_strategy`,
`compare_strategy_revision`, `preview_restore_strategy`, and
`publish_restore_strategy`. Validation returns normalized source, effective
resolution, source/effective review data, fingerprints, summary, and rule count
where applicable. Responses never include the expanded generated plan. Base
publication appends the next immutable revision under optimistic latest-
fingerprint protection. Strategy publication embeds its pinned Base snapshot,
appends the next immutable history revision, advances the fixed-name facade,
and remains separate from every strategy-selection or activation action. Stale
writes are conflicts; invalid source is a bad request. Publication paths append
operator-facing control-surface audit entries.

The native **Rename Strategy** affordance focuses the existing display-name
field and deliberately completes through normal Strategy review and
publication; IDs remain stable. `retire_strategy` is the backend operation
behind **Delete Strategy...**. It accepts only an exact custom Strategy ID and
the source fingerprint loaded by the client, refuses bundled/reserved or
currently selected Strategies, and moves the complete fixed-name publication
to a server-owned `retired` directory under the same advisory catalog lock.
The response returns retirement metadata and refreshed catalogs, not source,
generated plans, rules, executor actions, or a client-selected path. The
operation changes neither the control directive nor activation. Retirement
removes the latest facade from active catalogs but preserves immutable history.

Rebase preview is computed by the backend authoring resolver and shared diff
helpers. It reports Base entries added, removed, or changed; inherited effective
values that change; local overrides that remain unchanged; explicit ignores
that remain ignored; and resulting dependency/builder errors. A deterministic
review fingerprint binds any later changed Base pin to that exact reviewed
sparse source. Accepting the preview changes only the native client's draft;
normal Strategy validation and publication are still required.

The preview also supports attaching the first compatible Base to an existing
editable Strategy whose current source has no Base. The native client exposes
that choice, restores the published no-Base source when requesting the preview,
and blocks publication of the changed pin until the returned review fingerprint
is present. The published Strategy keeps its ID and receives a new version;
selection and activation remain unchanged.

### Immutable Strategy history and restore

The fixed custom Strategy directory contains a server-owned append-only
`history` directory and a server-owned `transactions` directory. History names
are derived only from validated IDs and monotonically increasing logical
versions. Each retained envelope contains the complete self-contained
publication—including its generated plan—and server-assigned publication
origin and audit identity. The history APIs deliberately redact that expanded
plan; runtime deliberately ignores history and continues loading only the exact
latest `<id>.profile.yaml` facade.

Publication uses one recoverable journal under the catalog writer lock. Linux
first validates the complete proposal, durably creates the journal and stages,
links the immutable revision into history and syncs that directory, then
replaces and syncs the latest facade as the commit point. Cleanup follows the
commit. Before that point, a handled failure restores the prior facade and
removes an uncommitted history link. On reopen, a valid journal plus retained
revision deterministically completes the exact facade; a journal without a
retained revision aborts and restores the former facade. Fingerprint mismatch,
symlink, duplicate version, unknown artifact, or external facade change fails
closed without overwriting evidence. Recovery runs before new publication, so
retries cannot create duplicate versions.

Opening the catalog conservatively adopts exact existing schema-1 and schema-2
latest publications without rewriting them. Unambiguous legacy retirement
archives may be represented in the same lineage; uncertain archives stay
unchanged and appear as catalog errors or warnings. History remains
authoritative for the next version even when the current facade is retired,
preventing an ID from silently restarting at version 1.

History summaries are newest-first and review-safe. They include status,
version/time, source/Base/resolution/plan/publication/revision fingerprints,
pinned Base, family/Tier, origin/audit identity, rule count, and current
validation/warnings. Linux computes comparisons for source directives,
effective values/provenance, embedded Base pin/snapshot, local overrides,
explicit Ignore entries, generated-plan fingerprint/rule count, metadata-only
changes, and current validation. The native client does no resolution or
semantic diffing.

**History** is available for active and retired custom lineages. Selecting a
historical revision requests a no-write restore preview bound to both that
revision fingerprint and the latest source fingerprint the editor opened.
Linux verifies the retained publication and rebuilds it with current trusted
code using its embedded historical Base snapshot, then binds the semantic
review to a third fingerprint. Only an explicit confirmation can publish that
intent as the lineage's next version. A stale latest, changed history revision,
or changed review returns HTTP 409; WPF preserves the open authoring draft and
refreshes history/latest catalogs only after a successful restore. Restore
never mutates history, selects or activates the Strategy, restarts automation,
changes Pause, or changes any control directive.

## Activity log audiences

The complete `logs/actions.log` remains the durable chronological stream. The
canonical level semantics are defined by the
[runtime action-log contract](../runtime_operations.md#action-log-contract).
The Operational view contains `ACTION`, `RESULT`, `WARN`, `ERROR`, and
`FAIL`; individual device actions and their evidence remain available through
`INPUT`, `DEBUG`, `MATCH`, and `STATE`.

Semantic summaries may carry optional presentation metadata in their paired
diagnostic detail. The API uses that metadata to give bundled Perk results a
short alias-based row and an exact itemized expansion without changing the
durable `RESULT` message. Older bundles without structured item metadata still
receive a compact count when their paired detail remains in the log tail.

The periodic operator heartbeat contains state, wave, and Coins/min. Its paired
`[STATUS_DETAIL]` diagnostic retains menu, secondary-state, and overlay
evidence. Until the planned atomic runtime snapshot replaces log-derived live
status, the Linux adapter accepts both this paired format and the earlier
all-in-one `STATUS` format so existing log tails remain usable across an
upgrade. The GUI presents only the latest status and a prior meaningful
transition outside the Operational activity list while retaining complete
status history in `Status only` and `All levels`.

The native client's default `Current run` scope uses the atomic
`logs/activity_scope.json` ledger. Automation startup creates it only when no
valid scope exists and otherwise reuses it, while verified Home `NEW_BATTLE`
preflight replaces it at the game-run boundary. It does not infer a run from
human-readable log messages. The runtime records the newest copied in-game
Battle History report as that scope's baseline and compares it after process
attachment. An unchanged report keeps the same Current run; a later report
starts a new scope at the visible continuity `ACTION`, including when the
battle was begun while automation was stopped. This is runtime-owned metadata
and does not change the activity API or native-client compatibility revision.
Activity responses include an opaque end cursor;
the client's non-destructive `Clear view` sends that cursor back as `after` and
can restore the complete selected scope at any time. Server revision 10
advertises this as the `current_run_activity_scope` capability.

## Windows host-performance telemetry

The native client owns host measurement because the relevant counters live on
Windows. A dedicated below-normal-priority thread samples once per second using
native system times, memory status, processor power information, and cached
BlueStacks process time/memory/I/O counters. BlueStacks process discovery runs
once per ten samples. One persistent PDH query also reads Windows `GPU Engine`,
`GPU Adapter Memory`, and `GPU Process Memory` wildcard counters. Its unmanaged
result buffers are reused, and the already scheduled process discovery supplies
names without another per-sample process scan. The path does not capture the
screen or launch PowerShell, WMI, `nvidia-smi`, or another process per sample.

Host and BlueStacks GPU utilization use the busiest-engine convention: values
from processes sharing a physical engine are combined, then the busiest engine
is reported and capped at 100%. Adapter-level dedicated/shared usage represents
host GPU memory; process-level usage supplies BlueStacks and competitor
attribution. Raw samples retain at most eight non-BlueStacks competitors.
Each ten-second aggregate publishes at most five, ranked first by maximum GPU
use and then GPU memory, with PID, process name, observation count, average and
maximum utilization, and maximum dedicated/shared memory. No per-process or
per-engine sample is published individually.

The client retains 120 raw samples in memory and reduces each ten-sample window
to averages and extrema. An ADB-port or run-identity transition closes the
current window early rather than mixing correlations. Each aggregate carries a
stable locally generated host ID, Windows host name, client session/sequence,
UTC window, logical-processor count, ADB port, and the run ID observed through
the status API. A run ID expires from new samples when status has not refreshed
for 15 seconds; outage telemetry remains available without being falsely
assigned to a later run.

Sampling can be paused and resumed only by the local native client. Pausing
closes and persists the current partial aggregate before the sampler waits;
the uploader remains active so previously queued evidence can continue
reconnecting and publishing. Resuming keeps the same host/session identity and
sequence, while the UTC window timestamps leave the intentional sampling gap
explicit. The enabled state is stored in the native client's local settings
and does not add Linux API control authority.

Aggregates first enter
`%LOCALAPPDATA%\TheTower\host-performance-pending.jsonl`. The bounded spool
keeps the newest 24 hours at the nominal ten-second cadence and reports any
drops in the GUI. Upload resumes in bounded batches after an API or tunnel
outage. Aggregate UUIDs are primary keys in
`logs/host_performance.sqlite3`, so retrying after a lost response is safe. The
Linux store also records the server's current run at ingest as separate
diagnostic context, keeps the sample-time run authoritative, and prunes records
after 30 days by default. Server revision 12 advertises capability
`host_performance_telemetry_v1`; server revision 13 adds
`host_performance_gpu_v1`.

The no-frame-telemetry target is below 0.5% average host CPU. Aggregate fields
include control-surface CPU and sampling duration so the Windows deployment can
verify that budget. GPU collection also records its own sampling duration.
Temperature and clock telemetry are not included because Windows does not
provide them through the same vendor-neutral counters. A later targeted
PresentMon provider should feed frame statistics into the same in-memory
aggregation/spool path, never emit one record per presented frame, and keep
total average CPU below 1%.

Control request examples:

```json
{"action": "pause"}
{"action": "pause", "minutes": 30}
{"action": "resume"}
{"action": "mode", "mode": "WAIT"}
{"action": "game_speed", "target": 4.0}
{"action": "game_speed", "target": 6.3}
{"action": "resolve_gate", "request_id": "...", "decision_id": "retry"}
{"action": "resolve_tournament_launch", "request_id": "...", "decision": "start"}
{"action": "resolve_tournament_launch", "request_id": "...", "decision": "cancel"}
{"action": "configure_run", "skip_checks": ["bots_preset"]}
```

Process request examples:

```json
{"action": "start", "run_state": "PAUSED", "startup_gate_policy": "auto_validate"}
{"action": "start", "run_state": "RUNNING", "startup_gate_policy": "auto"}
{"action": "stop"}
{"action": "restart_attached"}
{"action": "set_adb_port", "adb_port": 5565}
{"action": "set_strategy", "strategy": "tournament"}
{"action": "set_strategy", "strategy": "farm_t18", "apply_to_active_run": true}
```

## Current GUI capabilities

- Persistent indefinite and timed pause, including replacing or extending an
  existing timed pause.
- Resume and Game Over mode selection (`RETRY`, `WAIT`, or `HOME`). State and
  mode controls highlight the saved selection; amber means a live runtime has
  not yet acknowledged the latest directive.
- A distinct running-battle Strategy Action Gate banner based only on fresh,
  owner-matched structured status. It reads “Strategy actions blocked —
  observation and safe collectors remain active.” and shows the reason, failed
  checks, and collectors that currently retain authority. The Automation field
  and Pause coloring continue to show only requested/acknowledged control
  state; an active Strategy Gate is never labelled globally Paused. This gate
  status was introduced in server revision 22 with capability
  `strategy_action_gate_v1`.
- A discoverable custom Strategy **History** window for active and retired
  lineages. It shows immutable revision identity and current validation,
  requests Linux-computed semantic restore reviews, and enables restore-as-new
  only after a successful review and explicit confirmation. This history
  feature requires server revision 23 and capability
  `strategy_revision_history_v1`.
- Managed preset-or-local Strategy Authoring controls for the exact
  server-declared Module slots and family choices, complete ordered Target
  Priority membership, and three-field Orb Distance definition. Both forms'
  dormant drafts survive sparse Base and Strategy source-state changes while
  Linux remains normalization, resolution, review, history, and publication
  authority. This requires server revision 24 and capability
  `strategy_authoring_local_loadout_editors_v1`.
- Persistent numeric game-speed selection from `x0.0` through `x6.0` in
  `x0.5` increments, plus `x6.3` for maximum available. Lower values are exact
  targets across live and future runs. Both clients keep the custom-target
  warning visible and confirm before starting a managed runtime under it; the
  native client also distinguishes saved intent from runtime acknowledgement.
  This requires server revision 14 and capability `game_speed_target`.
- Native Windows host health for system CPU, memory, processor clock,
  BlueStacks CPU/RAM/process identity, and local publication state. Hovering
  the strip shows sampling cost, BlueStacks I/O, last Linux acknowledgement,
  and any sampler/spool/upload error. The display remains local and current
  while the API is unavailable.
- Automatic startup-gate decision dialogs for requests published by the
  runtime. The API accepts only an option contained in the matching pending
  request. Retry re-runs the check with fresh evidence; a bypass or configured
  fallback waives only the named requirement for the current run, so unrelated
  checks such as Auto Pick Perks remain authoritative. Closing the dialog
  leaves automation blocked and the request pending.
- Non-blocking attached-Tournament warning dialogs use the same scoped decision
  channel. They offer persistent Pause for manual changes, a fresh read-only
  retry, or continuation with only the displayed mismatch waived. Closing the
  dialog leaves the warning pending but does not block terminal observation.
- An optional **Configure run...** dialog populated from the selected
  strategy's actual preflight requirements. Unchecked checks retain their
  defaults; checked checks create strategy-bound one-run waivers. The dialog
  never opens automatically, saving does not start automation, and changing
  strategy clears staged exceptions.
- Complete automation-service start (paused or running) and stop through a
  fixed systemd user unit.
- Guarded **Reload automation for current battle** in the native and browser
  clients. It replaces the main Python process only after fresh owner and
  `RUNNING` evidence, verifies the attached replacement, restores the prior
  control state, and leaves failures paused.
- Automatic attachment to an existing/resumable battle on process start. The
  Process tab offers **Validate current battle if attached** or **Skip checks
  for current battle**. Validation is read-only and a repairable mismatch asks
  before the guarded battle restart/repair path is authorized. The choice has
  no effect at verified Home **New Battle**, where normal pre-battle checks
  always run. This requires server revision 15 and capability
  `automatic_battle_attachment`.
- Target and observed game speed are separate fields. Selecting a target
  persists operator intent and immediately re-arms enforcement during
  `RUNNING`; every periodic status frame independently reads the visible game
  speed without extra capture or input. The native and browser clients show
  both values, and retained Coins/min samples include the corresponding
  observed speed for mid-run analysis. This requires server revision 16 and
  capability `observed_game_speed`.
- Persistent ADB-port selection for the next managed start, plus live handoff
  while the runtime has acknowledged `PAUSED`. The API accepts only an integer
  TCP port; the runtime keeps Pause and its former target if new-target
  connection or screenshot validation fails.
- Validated strategy selection (`farm_t18`, `farm_t19`,
  `tournament`, or `none`). A stopped selection is saved for the next start;
  an active selection is queued for a confirmed run boundary by default. The
  native GUI separates a strategy dropdown from explicit **Use next battle**
  and **Switch this battle** actions while active. When stopped, **Save startup
  default** only persists the selection; Start already uses the visible
  selection. **Switch this battle** applies normal behavior and report identity
  after fresh active-battle evidence while deferring new-run gates. The
  dropdown preserves an unsent selection across
  status refreshes, action buttons disable requests that would be no-ops, and
  status reports selected, current, and pending strategies separately.
- Durable Tournament-validation status in both clients. It distinguishes Home
  preflight pending, ordinary-battle ownership, battle-only checks, cleanup,
  launch confirmation, and a failed/cancelled result with its reason. A ready
  result automatically opens an operator prompt that reminds the operator to
  set Target Priorities for the current Tournament Battle Conditions when the
  battle begins.
  **Start Tournament** performs lightweight receipt, configuration, runtime,
  and screen checks and authorizes one verified Tournament launch without
  rerunning validation. **Cancel launch** consumes only the automatic launch
  offer, while **Decide later** leaves it pending for the persistent review
  button. A manual Tournament start remains supported. This requires server
  revision 7 and capabilities `exclusive_strategy_validation_status` and
  `tournament_launch_confirmation`.
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
  resolved settings, game-speed target/timeline, and preflight evidence.
- Local filters for type, Tier, minimum/maximum wave, strategy, and quality.
- Local export of the currently filtered completed-battle summaries as UTF-8
  CSV.
- Draggable layout dividers across the operational control sections and between
  the history list and selected-battle report.
- Local persistence of the main and Battle History window positions, sizes, and
  maximized states. Invalid or off-screen placement is ignored, and minimized
  state is never restored.
- A per-Windows-session instance guard. A repeated launch restores and activates
  the existing operational window rather than creating competing clients.
- Independently refreshed recent activity that defaults to concise operational
  entries in the explicit current-run scope, with newest-entry following,
  non-destructive local clear/restore, and server-side diagnostic/all-level
  filters, without granting general log-file access.
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
4. Add targeted opt-in PresentMon frame telemetry through the existing
   in-memory host-performance aggregation path. Scope collection to the
   BlueStacks renderer, retain summaries rather than individual frames, and
   validate the combined one-percent CPU budget on Windows.
5. Add an optional current screenshot with capture time and a prominent stale
   watermark. It should remain read-only and rate-limited.
6. Add battle comparisons, trend charts, and aggregate rates by strategy, tier,
   profile, battle type, and date range.
7. Add opt-in notifications for battle completion, invalid capture quality,
   stale runtime, control acknowledgement timeout, and blocked preflight.
8. Extend next-start strategy selection to safe custom YAML plans. Such changes
   should show a resolved configuration diff before they take effect.
9. Support multiple ADB targets with independent authority, history, and health
   views.
10. If access expands beyond an SSH tunnel and one trusted operator, add TLS,
   named users/roles, request IDs, and a durable control audit log before adding
   more write operations.
