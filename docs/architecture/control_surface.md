# Control Surface Architecture

The primary control surface is a native Windows WPF application. A small
Linux-side service exposes the same repository-local controls and records used
by the automation and CLI. The earlier browser client remains a useful fallback
served by that same API.

```text
Native Windows app
      │ starts/stops passwordless Windows OpenSSH
      │ confirmed fixed SSH restart ──► thetower-control-surface.service
      │ local-forward (recommended)
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
  With confirmation, the client may use its validated SSH destination to run
  only the fixed `systemctl --user restart thetower-control-surface.service`
  command, then must reconnect and verify the complete compatibility contract.
  Restart reloads the installed Linux code but does not deploy an update. This
  path cannot select another unit or command and never restarts main
  automation.

Control writers use a companion advisory lock and atomic replacement. Timed
pause expiry revalidates its exact deadline while holding that writer lock, so
an operator extension or replacement with an indefinite pause wins over a stale
expiry attempt.

## Transport and access

The server binds to `127.0.0.1:8787` by default. The Windows app can launch and
own two passwordless OpenSSH processes: the existing Windows-local API forward
and a separately controllable ADB reverse forward. It invokes `ssh.exe` without
a shell, uses only validated destination/port fields, enables BatchMode and
forward-failure detection, and closes both processes when the app exits. The
equivalent manual commands are:

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
reported separately in the GUI.

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
| `GET` | `/api/v1/status` | Server revision/capabilities, control intent, acknowledgement, current-run identity, latest observation, and runtime evidence |
| `POST` | `/api/v1/control` | Allowlisted control mutation |
| `POST` | `/api/v1/process` | Start/stop or guarded-reload the fixed systemd automation unit, select its startup-gate policy, save/queue/adopt a bundled or published custom strategy, or configure/safely hand off its ADB port |
| `POST` | `/api/v1/host-performance` | Bounded, idempotent batches of native Windows host/BlueStacks performance aggregates |
| `GET` | `/api/v1/strategy-profiles` | Bundled/custom profile summaries plus the allowlisted Farm policy and preset catalogs |
| `POST` | `/api/v1/strategy-profiles` | Validate a constrained Farm draft or atomically publish its source and generated plan |
| `GET` | `/api/v1/battles?limit=N` | Newest Battle and Tournament summaries |
| `GET` | `/api/v1/battles/{battle_id}` | One full structured battle record |
| `GET` | `/api/v1/activity?limit=N&levels=ERROR,WARN&scope=current_run&after=CURSOR` | Recent structured action-log entries, optionally filtered by level, explicit run scope, and opaque clear-view cursor |

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
strategy IDs. The single document is written through a same-directory temporary
file, `fsync`, and atomic replacement while a companion advisory writer lock
serializes concurrent server requests. Updating an existing custom profile also
requires the source fingerprint that was current when the editor loaded it, so
concurrent or stale edits fail with a conflict rather than overwriting a newer
revision.

Custom publications live under `config/strategies/custom` and are ignored by
Git as operator-owned configuration. Profile IDs are restricted to fixed-name
lowercase identifiers and cannot collide with bundled or legacy names. There is
no API delete or arbitrary-path operation. Selecting or applying a published
profile remains a separate explicit action through the existing process API and
its normal next-boundary or active-battle semantics.

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
