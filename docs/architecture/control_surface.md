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
      ├── manage ► persisted localhost ADB registration
      ├── read  ──► logs/actions.log
      ├── read  ──► logs/activity_scope.json
      ├── read  ──► logs/strategy_action_gate.json
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
- Automation **Paused** blocks every automated device input while capture,
  detection, lifecycle observation, and status reporting may continue.
  Automation **Enabled** permits guarded actions; it does not assert that the
  observed game state is `RUNNING`. Home observation changes neither state.
- The GUI distinguishes a saved directive from runtime acknowledgement. It
  never presents a control-file write alone as proof that the runtime applied
  it.
- Interactive development uses that same separation. A lease request is only
  control intent; `/api/v1/status` reports it apart from the fresh
  runtime-owned acknowledgement and marks it active only when the exact
  runtime/session, PID, target, heartbeat, suppressive hold, and denied input
  matrix all agree.
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
- The allowlisted write surface is pause, timed pause, explicit enable,
  exact-evidence-bound Start Battle and Attach to Battle intent, Take Manual
  Control and Return Control, save-backed setup capture/review/save-as-new,
  future terminal policy,
  persistent numeric game-speed target selection,
  resolution of a runtime-published startup-gate decision,
  optional strategy-scoped one-run check configuration,
  one cooperative interactive-development lease request, heartbeat, and
  release,
  bundled or validated custom-strategy selection, constrained custom Farm
  profile publication, stopped or
  acknowledged-paused ADB-port configuration, and fixed managed-service
  start/stop. Active strategy
  requests are declarative runtime configuration, not direct tap authority.
  Profile publication writes only a fixed-name file beneath
  `config/strategies/custom`; it does not select, queue, adopt, start, restart,
  stop, pause, enable, or otherwise apply that profile.
  There is no arbitrary tap, shell command, process kill, direct Surrender,
  file-path, or ADB endpoint.
- Complete Stop persists `STOPPED` before asking the fixed systemd user service
  to stop. Start Automation launches the service under `PAUSED` with no battle
  workflow selected. It does not enable actions, start a battle, or attach to
  one. Repeating an already satisfied Start or Stop is an explicit no-op.
- For managed launches, the long-lived Linux control service is the sole ADB
  reconnect owner. It reads the same persisted port, starts or reuses the ADB
  server inside its own service lifetime, and maintains only that exact
  `localhost:PORT` across automation stop/start cycles. The automation unit
  explicitly selects observer mode and cannot start through the API if its
  installed unit does not advertise that ownership boundary. Direct manual
  runtimes retain their self-managed fallback. Complete Stop and guarded
  replacement synchronously refresh registration after the old process exits,
  covering a daemon that had originally been created inside its cgroup.
  Registration never grants frame or input authority; the runtime still
  requires its target lock and supported fresh capture.
- The public attached-reload action is retired. A process replacement is an
  explicit Stop Automation followed by Start Automation, fresh observation,
  and a separate matching battle intent; replacement never restores action
  authority or chooses attachment implicitly.
- Start Battle is available only from fresh, owner-matched Home `NEW_BATTLE`
  evidence. The runtime revalidates the same PID, target, target generation,
  activity scope, and boundary before acknowledging the request, then enters
  the ordinary new-run lifecycle and its normal gates. Attach to Battle is
  available only from fresh Home `RESUME_BATTLE` or active-battle evidence and
  never falls back to Start Battle. Attachment stays input-blocked before
  battle adoption while its exact forced-save identity validation is
  unresolved. A valid save advances it to `ready` as observation-only; the
  battle is adopted only after lifecycle confirmation. Selecting a Strategy
  for that battle is a later explicit action and never grants Surrender.
- Automation Enable alone never substitutes for that initial battle intent.
  While the process is waiting for Start Battle or Attach to Battle, Home
  observation may continue but ordinary Home save/configuration preflight,
  legacy auto-start, and one-shot validation launch remain input-blocked. A
  freshly visible five-gem Home claim may run as the sole `home_ad_gem`
  auxiliary exception while Enabled and no immediate battle workflow exists.
  It synchronizes current control and operator-workflow ownership, then
  rechecks Pause and typed authority before input; a Start/Attach request that
  arrives after scheduling therefore cancels the claim. The exception grants
  no navigation, setup, Strategy, Battle, or Resume Battle authority. A stale
  acknowledged Start ledger cannot dispatch unless the current MissionManager
  also owns that exact initial intent.
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
  Fresh evidence that a Tournament already started cancels an unclaimed
  receipt as obsolete; Tournament Results repeats that fail-safe. A managed
  Start therefore cannot leave validation planned after the attached
  Tournament completes. An attached non-Tournament battle retains the request
  for a later verified Home boundary before Tournament start.
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

## Better Control Model

Server revision 30 retains `better_control_model_v1` and
`save_backed_setup_capture_v1` for additive compatibility and advertises
`better_control_model_v2` plus `save_backed_setup_capture_v2`. The additive
`control_model` status object
keeps five dimensions independent:

| Dimension | Values and authority |
| --- | --- |
| Process lifecycle | `stopped`, `live`, or `unavailable`; only Start/Stop Automation changes it |
| Action authority | requested directive, runtime acknowledgement, and effective `paused`, `enabled`, `pending`, `stopped`, `unknown`, or `unavailable` |
| Observed game | fresh/stale/unavailable evidence classed as Home New Battle, Home Resume Battle, active battle, Game Over, Tournament Results, or unknown |
| Strategy scope | startup default, active-battle Strategy, and pending next-boundary Strategy |
| When this battle ends | continue automatically, wait, or return/stay Home; `NEXT_BATTLE`, `WAIT`, and `HOME` remain compatibility values only |

The status also carries exact workflow evidence, durable battle/manual-control
ledgers, and per-action `available`, stable `code`, and operator-facing
`reason`. A client disables unavailable actions but the server independently
rechecks every request. Missing, stale, wrong-owner, wrong-target, changed-
generation, changed-scope, and mismatched-state evidence fails closed. A fresh
authority heartbeat cannot renew the nested game observation: both timestamps
must remain inside their freshness windows. Malformed control JSON makes every
Better Control Model action unavailable with `control_invalid`.

State and terminal-policy directives carry separate request IDs. Runtime log
acknowledgements include the applied ID, and status considers an acknowledgement
current only when both value and request ID match. Repeating an unacknowledged
same-value request reports `pending` without rewriting its identity; a stopped
or exactly acknowledged repeat is a visible no-op.

### Command and transition matrix

| Process | Effective authority | Fresh observed game | Explicit request | Result |
| --- | --- | --- | --- | --- |
| Stopped | unavailable/stopped | any | Start Automation | launch service Paused; await observation and battle intent |
| Live | paused | Home New Battle | Start Battle | `requested` → `awaiting_enable`; explicit Enable revalidates and acknowledges normal new-run gates |
| Live | enabled | Home New Battle | Start Battle | revalidate and acknowledge normal new-run gates |
| Live | enabled | Home New or Home Resume with no exact immediate workflow grant | terminal-policy change, Enable, Strategy selection, or no additional request | observe and claim only a freshly visible five-gem Home reward through the typed `home_ad_gem` collector; do not serialize ordinary Home preflight, run configuration setup, claim validation, recover Home, dispatch a battle control, or run any other collector |
| Live | enabled | Home New Battle with a terminal-bound continuation | no new request | revalidate the exact terminal-time state/policy request IDs, runtime, target generation, activity scope, and New Battle control; run normal new-run gates, dispatch exactly one verified New Battle, and consume the claim only after successful dispatch |
| Live | enabled | verified Home control was tapped | acknowledged Start or ready resumable Attach | record `action_dispatched`; keep unrelated automation suppressed until the same battle is adopted, a definitive mismatch interrupts, or the 20-second launch window fails |
| Live | paused | Home Resume Battle or active battle | Attach to Battle | `requested` → `awaiting_enable`; explicit Enable enters `validating_save` without adopting the battle |
| Live | enabled | Home Resume Battle or active battle | Attach to Battle | prefer a stable exact-target save; if its source is safely restored but the data/mapping is unusable, bind guarded Battle History instead; then become observation-only `ready` without selecting a Strategy |
| Live | either | Game Over, Tournament Results, unknown, stale, or mismatched evidence | Start Battle or Attach to Battle | reject as unavailable/mismatched; never substitute the other workflow |
| Live | enabled or paused | any fresh exact state | Take Manual Control | atomically request indefinite Pause; become `active` only after runtime acknowledgement |
| Live | paused and manual control `active` | Home New, Home Resume, active battle, or Game Over with exact target/scope binding | Return Control | remain Paused; record passive observation; await explicit Enable |
| Live | paused and manual control `active` | Tournament Results, unknown, or incomplete exact binding | Return Control | unavailable; no save-backed Return route is advertised or substituted |
| Live | paused, Return requested | refreshed observation | Enable | enter input-blocking `reconciling`; prefer a new forced save (or a bound natural Game Over save), then automatically use the supported active/Home/terminal UI route if that save is unusable after safe restoration |
| Live | reconciling Return | source restoration, owner, target, scope, or authority binding is lost after lifecycle input | no additional request | persist Automation Paused and terminalize that Return as failed/interrupted; do not repeat lifecycle input or open UI from an unsafe boundary |
| Live | enabled, adopted active battle | active battle | apply selected Strategy to this battle | adopt only after explicit selection; preserve battle identity and defer new-run/Home-only gates; Surrender remains unauthorized |
| Live | enabled, adopted active battle | repair-only mismatch | choose **Surrender this battle and repair setup** in the runtime gate | grant one exact-battle, exact-reason Surrender; write the nonrepresentative disposition before verified Home, then let normal Home repair and the separately selected future-battle policy continue without an implicit Pause |
| Live | enabled | Home New, Home Resume, or active battle with exact binding | Capture current setup as… | force a new save, present captured and unresolved fields for review, then save a new inactive Module preset or Strategy draft without selecting, queueing, publishing, or applying it |
| Live | paused, Return awaiting trusted-mismatch review | same exact active battle with its process-local forced acquisition retained | Capture current setup as… | project the Return acquisition without new input, label that provenance explicitly, and leave Return Control Paused and unresolved after any capture save |
| Live | capture owns a forced refresh | compatible exact/forward save revision | no additional request | use only the resolved mapping's explicit compatibility allowlist; preserve every other setup field as unresolved |
| Live | capture owns a forced refresh | source restored, but mapping/projection/acquisition is unavailable or round identity is incomplete | no additional request | report `unavailable`, open no configuration UI, and preserve the prior action-authority state |
| Live | capture owns a forced refresh | fresh active/resumable evidence contradicts the requested battle identity | no additional request | report `failed` and enter a running-battle Strategy Gate so observation and safe gem collectors continue while strategy/lifecycle input yields |
| Live | capture owns a forced refresh | fresh Home New evidence contradicts the requested boundary, or an attempted lifecycle transition cannot prove source restoration | no additional request | report `failed` and persist Automation Paused because the safe input source is unproven |
| Live | capture owns or completed a forced refresh | ready/terminal ledger write fails | no additional request | retain the exact process-local result and retry only its atomic receipt without changing action authority or serializing again |
| Live | capture has a terminal result | `saved`, `cancelled`, `unavailable`, `interrupted`, or `failed` | reopen Capture | inspect the prior result only; a new serialization requires the separate explicit **Try capture again** action |
| Live | enabled | Game Over | selected future terminal policy | collect terminal data best effort, then follow Retry/Home; if the route fails, retain it for a fresh-evidence retry without changing authority |
| Live | enabled | Tournament Results | selected future terminal policy | `WAIT` retains the screen; Continue/Home first capture the result and use the verified dismissal route; failure retries from fresh evidence without changing authority; only Continue already selected for that terminal boundary can carry one exact launch through verified New Battle Home |
| Live/stopped | already satisfied | any | repeated Pause, Enable, Start Automation, Stop Automation, terminal policy, or Take Manual Control where defined | return a visible no-op instead of fabricating a transition |

An intent requested under Pause is pending, not acknowledged action authority.
If the runtime, target, activity scope, or observed boundary changes before
acknowledgement, the request becomes `rejected` or `interrupted`. Stop and a new
process boundary interrupt unfinished workflows. Home alone never enables or
pauses input.

At Tournament Results, `WAIT` is satisfied by retaining the screen. Continue
automatically and Return/stay Home first preserve the result, then use the
verified OK-to-Home dismissal owner. A failed dismissal retains the selected
future policy and retries from fresh terminal evidence without changing action
authority. Continue still does not make the policy control an immediate battle
command. If Continue was already selected when this exact terminal boundary
was handled, successful dismissal may freeze one process-local continuation
claim for the next verified New Battle Home. Selecting Continue after a
retained result can dismiss that result, but it does not retroactively create a
Home launch claim.

Managed Home launch authority is deliberately narrower than terminal policy.
An ordinary Game Over under Continue uses its direct Retry control. A route
that must pass through Home—No Strategy post-run collection, an explicitly
authorized configuration-repair return, or Tournament Results dismissal—may
carry a one-shot claim created from the exact terminal observation. The claim
is bound to runtime/PID, ADB target and generation, activity scope, and the
state and mode request identities in force at that terminal. It survives only
its owned Home work, requires fresh `NEW_BATTLE`, and is consumed only after a
verified dispatch. Policy or authority request changes, manual/workflow
supersession, Resume Battle, owner/target/scope change, process replacement, or
unexpected manual activity discard it. Being at Home, selecting a Strategy,
or changing **When this battle ends** never creates one.

The API retains `resume` as a deprecated alias for `enable` and the old
directive-only `stop` for internal coordination compatibility. The latter sets
authority `STOPPED` but does not manage the systemd process. Revision-28 and
later browser, native, and CLI clients do not expose either spelling; operator
process lifecycle uses `/api/v1/process` Start/Stop Automation.

Take Manual Control is not merely a label for Pause. Its durable request owns
an indefinite Pause and exposes `pause_requested` until the runtime applies it.
Return Control is also not Resume: it records a separate return request while
Pause remains authoritative, then requires an explicit Enable request and an
exclusive reconciliation hold. Running and resumable returns prefer a newly
forced exact-target save; Home New prefers the normal Home serializer, and
Game Over prefers the bound natural terminal acquisition. If that save is
absent, unsupported, structurally incompatible, or unprojectable after safe
source restoration, the runtime automatically uses the complete supported UI
route for that boundary and writes an exact-bound UI reconciliation receipt.
A loss of restoration, owner, target, scope, or action authority terminates
the exact workflow and leaves Automation Paused rather than opening UI from
cached data. Home New terminalizes that unsafe outcome once, so later
heartbeats do not background the game again. A trusted mapped mismatch remains
distinct from unusable save data and requires explicit review and another
Enable before a new refresh.
If a bounded Home configuration repair then cannot make stable progress, the
manual-control ledger advances to `awaiting_manual_correction` with the exact
failed check, reason, retryability, and retained forced-save receipt. Clients
show that failure while Automation remains Paused. After the operator makes
the reported correction, a new explicit Enable discards prior private claims
and requests another serialization; a heartbeat never retries it on its own.
A Pause, Stop, or Take Manual Control that arrives during Home setup yields at
the first denied input without cleanup. Only a later same-owner Enable may
restore Home from that yielded route.
Outside such an already-owned route, managed Home setup is authorized only by
an acknowledged exact Start Battle request, an exact terminal-bound
continuation, or the separate one-shot validation owner. Terminal policy,
Strategy selection, prior battle history, and Automation Enabled are not Home
navigation or recovery authority.
That owner is not a snapshot carried across setup: save acquisition and Home
configuration work revalidate the original workflow/manual request identity,
intent, status, and typed lifecycle authority before retaining completion.
The runtime checks them again before Home handling and supplies the same exact
revalidator to the verified tap's final input boundary. A replacement request,
Pause/Stop, manual-control handoff, changed policy, or superseding workflow
sends no battle input and cannot inherit pending carried-save launch evidence.
Unexpected active-battle → Home Resume Battle activity while Enabled enters the
same safe Pause/manual-control ledger rather than competing for input. Broader
manual-activity detection and grace-period controls remain separately
backlogged.

Take Manual Control also records the operator's terminal-collection choice.
The default `minimal` choice detects manual Surrender from the natural save,
writes a nonrepresentative excluded record, and performs no terminal UI
collection. `full` opts that manual Surrender into the ordinary terminal
collection path. Neither choice authorizes automation to Surrender, and there
is no generic manual Surrender button.

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

Forward persistence and target-registration persistence are independent. The
Windows tunnel host keeps a desired reverse SSH listener alive after the GUI
closes. The Linux control service keeps its ADB daemon and selected TCP target
registered after automation stops. API status exposes the latter as
`adb_connection` (`unknown`, `device`, `unavailable`, or
`configuration_error`) with the exact target, bounded retry state, last check,
and configuration error. Neither an active forward nor a `device` row proves a
valid emulator frame.

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
| `GET` | `/api/v1/status` | Server revision/capabilities, Better Control Model dimensions/workflows, control intent, acknowledgement, current-run identity, current save-backed Perks, latest observation, structured Strategy Action Gate, and runtime evidence |
| `POST` | `/api/v1/control` | Allowlisted control mutation |
| `POST` | `/api/v1/interactive-development-lease` | Request, heartbeat, or release the one cooperative development lease; never dispatch device input |
| `POST` | `/api/v1/process` | Start/stop the fixed systemd automation unit independently of battle intent, save/queue/adopt a bundled or published custom strategy, or configure/safely hand off its ADB port |
| `POST` | `/api/v1/host-performance` | Bounded, idempotent batches of native Windows host/BlueStacks performance aggregates |
| `GET` | `/api/v1/strategy-profiles` | Bundled/custom profile summaries plus the allowlisted Farm policy and preset catalogs |
| `POST` | `/api/v1/strategy-profiles` | Validate a constrained Farm draft or atomically publish its source and generated plan |
| `GET` | `/api/v1/strategy-authoring` | Registry metadata, separate Base/Strategy catalogs, authoritative merged Module preset details, editable source, effective resolution/provenance, compatible Base revisions, capabilities, and catalog errors |
| `GET` | `/api/v1/strategy-authoring/history` | Newest-first immutable custom-Strategy lineage and revision summaries, including retired lineages, without expanded plans |
| `GET` | `/api/v1/strategy-authoring/history/{id}` | One custom Strategy lineage and its ordered revision summaries |
| `GET` | `/api/v1/strategy-authoring/history/{id}/{version}` | One retained revision's review-safe source, Base snapshot, resolution, fingerprints, audit identity, and validation state without its generated plan |
| `POST` | `/api/v1/strategy-authoring` | Validate or publish Base/Strategy source, preview a Base pin, create an immutable custom Module preset, compare retained revisions, or review/confirm restore-as-new, without activation |
| `GET` | `/api/v1/setup-capture` | Current runtime-issued capture status, availability, inactive captured-draft catalog, Module presets, and comparison Bases |
| `POST` | `/api/v1/setup-capture` | Request a new forced-save capture, review a fingerprinted captured-versus-Base difference, save through the existing Module/Strategy owner, or cancel; never activate or publish |
| `GET` | `/api/v1/setup-capture/drafts/{id}` | Reopen one immutable captured Strategy source in the ordinary authoring editor without selecting or activating it |
| `GET` | `/api/v1/battles?limit=N` | Newest Battle and Tournament summaries |
| `GET` | `/api/v1/battles/{battle_id}` | One full structured battle record |
| `GET` | `/api/v1/activity?limit=N&levels=ERROR,WARN&scope=current_run&after=CURSOR` | Recent structured action-log entries, optionally filtered by level, explicit run scope, and opaque clear-view cursor |

### Current battle Perk status

Server revision 31 advertises `current_battle_perks_v1` and adds the
`current_battle_perks` status object. The runtime's existing Perk timeline
owner writes a compact presentation beside its atomic same-run checkpoint only
after the shared save monitor accepts a complete exact prefix. It collapses
that prefix to one row per semantic Perk, records the current level and most
recent saved selection wave, and orders rows by most recent selection. The
object also retains the checkpoint capture time, saved wave, total pick count,
and unique count so the client never presents a passive save as newer than it
is.

The adapter accepts the presentation only when its checkpoint schema is
supported and `activity_scope_run_id` exactly matches the atomic current-run
ledger. A missing checkpoint reports `awaiting_save_checkpoint`; an absent
current run or malformed projection reports `unavailable`; and every such
result contains an empty item list. A scope transition therefore hides the old
battle immediately even if the runtime has not yet written the new battle's
first save checkpoint. This is a read-only projection: the API performs no
save acquisition, serialization, panel navigation, device input, or action-
authority decision.

### Structured Strategy Action Gate status

`GET /api/v1/status` exposes `strategy_action_gate` separately from
`control.state`, state acknowledgement, and the latest observation. The object
reports availability, active/inactive and stale state, age, owner match,
strategy and battle scope, source/phase, failed check IDs, operator reason,
activation/update times, Pause/Stop context, active exclusive holds, the four
typed authority decisions, any optional collector allowlist declared by a
hold, currently allowed auxiliary collectors, and any exclusive auxiliary
route.

The adapter reads only the runtime-owned atomic snapshot. It rejects malformed
or unsupported schemas, inactive publishers, expired timestamps, and PID/ADB
owners that do not match an active runtime lock. Missing or stale evidence is
reported as unavailable/stale and cannot be promoted into action authority.
Warning text in the action log is never parsed as gate state. Adding this
field is backward compatible: earlier clients ignore it and retain every older
endpoint and capability. `home_ad_gem` is an additive collector value and a
conformance repair to the existing `better_control_model_v2` capability; it
adds no command, endpoint, or client-side authority inference.

### Interactive development lease status

Server revision 26 advertises `interactive_development_lease_v1`. The additive
`POST /api/v1/interactive-development-lease` operation model is:

```json
{"operation": "request", "owner_label": "bounded task label"}
{"operation": "heartbeat", "lease_id": "ordinary coordination ID"}
{"operation": "release", "lease_id": "ordinary coordination ID"}
```

There is no client-supplied runtime, PID, target, timeout, source fingerprint,
secret, action, or command. The server derives the binding from a fresh
runtime-owned authority snapshot that matches the active OS lock. A live
conflict returns HTTP 409 with code `busy`; expired, terminal, or wrong-ID
heartbeats and releases are rejected, and a heartbeat additionally requires
fresh matching runtime ownership. Heartbeats extend the fixed 30-second expiry
without adding an action-log entry.

`GET /api/v1/status` exposes `interactive_development_lease.request` from the
control directive and `runtime_acknowledgement` from the atomic runtime-owned
authority snapshot. Its derived `active` flag additionally requires RUNNING
operator control, an unexpired request, fresh matching runtime/lock ownership,
an `active` acknowledgement for the same lease, the
`external_development` hold, continued observation authority, and denial of
auxiliary, strategy, lifecycle, and all allowlisted collector input. Pending,
release-blocked, expired, stale, or mismatched evidence is visible but never
active. The endpoint provides coordination state only. The separately delivered
development-side lease-aware ADB helper consumes `active` as the canonical
production policy decision and does not rederive it from
`strategy_action_gate`, `runtime.instances`, duplicated acknowledgement views,
or individual authority decisions. The helper still validates the supported
schema/capability, RUNNING control state, supplied request/acknowledgement lease
ID and lifecycle states, matching runtime identity and exact target, and the
same acknowledged expiry window. Immediately before input, production
`server_time` must leave the action's complete bounded subprocess timeout plus
the documented timing margin. This consumer does not add an input operation to
the control-surface API.

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

Server revision 25 preserves `strategy_authoring_v1`,
`strategy_authoring_specialized_editors_v1`,
`strategy_authoring_profile_lifecycle_v1`, `strategy_action_gate_v1`, and every
older capability, retains `strategy_revision_history_v1`, and advertises
`managed_custom_module_presets_v1` after revision 24 added
`strategy_authoring_local_loadout_editors_v1`. The additive
`/api/v1/strategy-authoring` endpoint implements the sparse Base/Strategy model
without changing `/api/v1/strategy-profiles`, `strategy_profile_catalog_v1`, or
`strategy_profile_editor_v2`. Pre-authoring native clients therefore keep using
their existing latest-only facade. The revision-23 authoring client retains its exact
preset-only metadata path against the newer service. Revision-24 option and
local-editor wire shapes remain unchanged. The revision-25 client requires the
retained authoring, Strategy Gate, history, local-loadout, and managed Module
preset capabilities and fails compatibility clearly against an older resident
service.

The GET response carries the setting registry, normalized initial values,
behavior-free specialized-editor metadata, safe editor catalogs, separate Base
and Strategy collections, normalized source documents, effective resolution
and provenance, latest compatible Base revisions, structured capabilities, and
catalog errors. The Module entry links to a merged detail catalog whose items
carry ID, display name, bundled/custom origin, immutable editability state,
normalized definition, and all eight ordered slot labels/families/roles/Module
assignments. Metadata declares choices, object fields, list constraints,
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

Bundled Module presets remain in immutable `config/loadouts/modules.yaml`.
Custom presets use the server-owned, Git-ignored
`config/loadouts/custom/modules` directory (or an injected disposable root)
with fixed names, bounded no-follow reads, locking, durable atomic create, and
collision rejection. One deterministic merged catalog supplies registry
options, detail metadata, legacy summaries, and prospective resolution; custom
IDs cannot shadow bundled IDs. Neither requests nor responses contain catalog
paths.

The WPF Module shared-preset form shows all eight authoritative slot
assignments; it labels bundled presets read-only and custom presets immutable.
**Create variant** can copy either origin and **Save as preset** submits the
current metadata-driven local definition. Both are save-as-new: no overwrite,
rename, deletion, or retirement exists. A successful create refreshes options
incrementally, preserves every retained selection object, and explicitly moves
the current row to the new preset while leaving Validate → Review → Publish
pending. A failed create preserves the draft and selections. The controls are
hidden without the managed capability.

POST accepts `validate_base`, `publish_base`, `validate_strategy`,
`publish_strategy`, `preview_rebase`, `retire_strategy`,
`compare_strategy_revision`, `preview_restore_strategy`, and
`publish_restore_strategy`, plus `create_module_preset`. The creation payload
contains a new safe ID, display name, and exactly one `{preset: id}` or
`{local: definition}` source. Linux returns HTTP 400 structured validation or
HTTP 409 collision errors without a partial file. Success returns the created
detail and refreshed catalog with `published: false`; it never publishes,
selects, or activates a Base or Strategy. Validation returns normalized source,
effective resolution, source/effective review data, fingerprints, summary, and
rule count where applicable. Responses never include the expanded generated plan. Base
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
[action-log contract](../action_log_contract.md).
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
{"action": "enable"}
{"action": "start_battle"}
{"action": "attach_battle"}
{"action": "take_manual_control"}
{"action": "return_control"}
{"action": "terminal_policy", "policy": "WAIT"}
{"action": "game_speed", "target": 4.0}
{"action": "game_speed", "target": 6.3}
{"action": "resolve_gate", "request_id": "...", "decision_id": "retry"}
{"action": "resolve_tournament_launch", "request_id": "...", "decision": "start"}
{"action": "resolve_tournament_launch", "request_id": "...", "decision": "cancel"}
{"action": "configure_run", "skip_checks": ["bots_preset"]}
```

Process request examples:

```json
{"action": "start"}
{"action": "start", "strategy": "farm_t18"}
{"action": "stop"}
{"action": "set_adb_port", "adb_port": 5565}
{"action": "set_strategy", "strategy": "tournament"}
{"action": "set_strategy", "strategy": "farm_t18", "apply_to_active_run": true}
```

## Current GUI capabilities

- Persistent indefinite and timed Pause, explicit Automation Enabled, and
  requested-versus-acknowledged state. The text defines Paused as zero
  automated input while observation continues and does not describe Enabled
  as the game being in `RUNNING`.
- Separate Start/Stop Automation, exact-state Start Battle/Attach to Battle,
  and Take Manual Control/Return Control controls. Their availability and
  pending/acknowledged/rejected/interrupted state comes from Linux, not local
  GUI inference. Start Automation always leaves actions Paused. This contract
  requires server revision 30 and capability `better_control_model_v2`;
  save-backed capture additionally requires `save_backed_setup_capture_v2`.
- A read-only **Perks** tab showing the current run's monitor-validated saved
  inventory, level, and last selection wave in most-recent-first order. It
  shows the checkpoint wave and local capture time, preserves an unchanged
  scroll position across ordinary five-second status refreshes, and clears on
  an unavailable or changed activity scope. The current native client requires
  server revision 31 and capability `current_battle_perks_v1`.
- Take Manual Control selects default minimal or opt-in full collection for a
  later save-confirmed manual Surrender without granting Surrender authority.
  **Capture current setup as…** shows fresh-save captured values, unresolved
  rows, and a fingerprinted Strategy/Base review, then saves only an inactive
  artifact. Captured Strategy drafts remain reopenable in the ordinary native
  authoring catalog together with their own immutable origin, difference, and
  unresolved review—not whichever capture happens to be current. A trusted-
  mismatch Return Control may supply its exact
  still-retained forced acquisition without a second refresh; the client shows
  that provenance, and capture completion does not complete Return Control.
- **When this battle ends** selects continue automatically, wait, or
  return/stay Home. The compatible `NEXT_BATTLE`, `WAIT`, and `HOME` values
  remain visible only as runtime representation; none is presented as an
  immediate battle command. Continue normally owns direct Retry at the next
  Game Over. A terminal route that necessarily returns Home can create the
  exact one-shot continuation described above; the selected value alone never
  does. Legacy `RETRY` normalizes to `NEXT_BATTLE`. This contract retains
  capability `terminal_dispositions_v2`.
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
- Authoritative eight-slot Module preset previews plus immutable custom
  **Create variant** and local **Save as preset** workflows. Linux owns the
  merged catalog and validation; native refresh keeps retained selections and
  never bypasses ordinary draft review/publication. This requires server
  revision 25 and capability `managed_custom_module_presets_v1`.
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
- Complete automation-service Start and Stop through a fixed systemd user unit,
  independent of action authority and battle workflow. The earlier attached
  reload and automatic-attachment controls are intentionally absent.
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

The production control-surface service now coordinates one cooperative
interactive development lease through the existing JSON/HTTP directive and
runtime-owned status paths. The development-side exact-target input helper is a
separate consumer of that status; it does not add arbitrary tap or ADB routes
to this service. Neither an acknowledged lease nor a worktree-local lock makes
ad-hoc worker input a supported path. Bounded read-only ADB operations still
need no lease after the normal live startup inspection. The complete
coordination contract is defined in
[development_isolation.md](development_isolation.md).

These are the next useful additions, in approximate priority order:

1. Publish a small atomic runtime-status JSON snapshot directly from the
   automation. This should include an observation sequence/time, current UI
   state, battle identity, wave, strategy/profile, action gate, active handler,
   and last error. It will replace action-log parsing as the primary live view.
2. Add recovery-timer controls such as extend, cancel, and return-now only after
   those operations have explicit runtime directives and freshness/authority
   checks. The GUI must not implement them as direct taps.
3. Extend the implemented active-battle → Home Resume Battle safety yield to
   broader likely manual-player activity, then show configurable grace-period
   countdown and ownership in the GUI.
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

Repository implementation of save-backed Attach/Return reconciliation and
**Capture current setup as…** is included in revision 29. Revision 30 adds the
typed capture authority outcome, inspect-without-retry terminal presentation,
and separate explicit retry action under `save_backed_setup_capture_v2`. The
Better Control Model backlog retains the unperformed Windows usability and
natural-boundary live validation; those checks are not implied by the
repository checkpoint.
