# Control Surface Architecture

The primary control surface is a native Windows WPF application. A small
Linux-side service exposes the same repository-local controls and records used
by the automation and CLI. The earlier browser client remains a useful fallback
served by that same API.

```text
Native Windows WPF app
      ├── opt-in BlueStacks maintenance ──► exact ADB-listener PID / HD-Player.exe
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
- Native Pause/Enable/policy clicks serialize independently from polling. The
  client cancels a stale status GET and sends the control POST immediately,
  then waits to render until the older response is drained. On Linux, that
  write shares the final cross-process boundary with input dispatch. One ADB
  command that already crossed its last guard, or mandatory restoration after
  lifecycle input, may finish; once the write is accepted no next compound
  step or new input can start.
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
  one detector-authorized BlueStacks maintenance handshake bound to the exact
  runtime, ADB target, and canonical battle identity,
  bundled or validated custom-strategy selection, constrained custom Farm
  profile publication, stopped or
  acknowledged-paused ADB-port configuration, and fixed managed-service
  start/stop. Active strategy
  requests are declarative runtime configuration, not direct tap authority.
  The profile-publication endpoint writes only a fixed-name file beneath
  `config/strategies/custom`; it does not select, queue, adopt, start, restart,
  stop, pause, enable, or otherwise apply that profile. After that endpoint
  confirms a Strategy publication or restore, the native client separately
  submits the ordinary `set_strategy` request for the next boundary when the
  process is active; when stopped it selects the Strategy for Start without
  changing the saved default. Base publication submits no control request.
  There is no arbitrary tap, shell command, process kill, direct Surrender,
  file-path, or ADB endpoint.
- Complete Stop persists `STOPPED` before asking the fixed systemd user service
  to stop. When fresh exact evidence proves that automation currently owns an
  active battle, Stop also retains a one-shot active-battle handoff. This applies
  whether automation started that battle or attached to it later; wave changes
  do not change the battle identity. A later Start launches under `PAUSED`,
  creates a fresh ordinary Attach workflow, restores Enable, and returns only
  after forced serialization compares the current `ActiveRoundIdentity` with
  the retained one. Equality reattaches the same battle; a later identity
  discards old battle-local state and completes the same normal Attach for the
  successor. If no such handoff exists, Start remains Paused with no battle
  workflow selected and does not enable actions, start a battle, or attach to
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
- The public one-step attached-reload action remains retired. Process
  replacement uses the durable Stop Automation then Start Automation boundary.
  A retained handoff never replays old authority: the replacement must own the
  same ADB target, issue a fresh Attach, and force-prove the current active
  identity against the exact pre-Stop identity. Equality reattaches it; a later
  identity attaches the successor. An ended battle, target mismatch,
  unavailable proof, or failed Attach leaves Automation Paused for explicit
  intent.
- Start Battle is available only from fresh, owner-matched Home `NEW_BATTLE`
  evidence. The runtime revalidates the same PID, target, target generation,
  workflow operation, and boundary, then forces a save that must prove no
  active round before dispatch. After the tap it remains input-blocked until
  first stable `RUNNING` forces and binds the new `ActiveRoundIdentity`.
  Activity-log scope changes never reject the request. Attach to Battle is
  available only from fresh Home `RESUME_BATTLE` or active-battle evidence and
  never falls back to Start Battle. Attachment stays input-blocked before
  battle adoption while its exact forced-save identity validation is
  unresolved. The accepted request freezes the complete selected Strategy
  definition. After lifecycle confirmation, No Strategy becomes an intentional
  observer; a proven kind/tier-compatible selection becomes the active
  Strategy; and an incompatible or unprovable selection becomes a degraded
  observer while remaining pending for the next safe boundary. Attachment
  never grants Surrender or current-battle configuration repair. A later
  active-battle Strategy request cannot convert that degraded observer: Linux
  atomically retains the same request identity but changes its apply mode to
  the next boundary, and the native client disables **Switch this battle**
  while that observer state is reported.
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
  pending request. When no different request is pending, a same-ID request is a
  no-op only when the latest resolved definition matches the loaded definition;
  a newly published same-ID revision remains pending for the same guarded
  boundary installation.
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
  only when fresh `RUNNING` or Home `RESUME_BATTLE` evidence carries the
  current force-bound `ActiveRoundIdentity`. The control directive binds both
  its request ID and that exact battle identity; Linux compares the binding
  again immediately before adoption. If Pause, manual play, or another
  boundary replaces or clears the identity, the exact request is atomically
  downshifted to the next safe boundary instead of transferring to the later
  battle. Adoption changes normal strategy behavior and the strategy/profile
  identity used by Battle End reporting, but uses attachment semantics: run
  initialization, session preflight, and Home-only gates remain deferred until
  the next genuine new-run boundary, except for an explicitly declared
  read-only observer check. If `NEW_BATTLE` is observed first, the request
  follows the normal boundary-install path and all new-run gates remain active.
- The API never accepts an arbitrary executable, service name, shell command,
  or process-mutation target from the Windows client. The Linux server is
  configured with one validated unit name. BlueStacks maintenance is the
  narrow exception for process-identity evidence: the host acknowledgement
  reports the exact Windows listener PID/start time and the completion reports
  its replacement, but Linux never executes either identity. Windows resolves
  and revalidates the configured listener and executable locally before any
  stop or start.
- A malformed control file is reported and preserved rather than overwritten.
- Status advertises an API version, a monotonic server revision, and explicit
  capabilities. The Windows client evaluates all three: it requires the
  expected API version, a compiled minimum server revision, and its required
  capabilities. A feature that makes the current Windows client depend on new
  Linux behavior must advance the Linux server revision and the client's
  minimum revision in the same change; independently gated features should
  also advertise and require a named capability.
- Connecting the Windows client remains read-only unless the operator has
  explicitly enabled automatic BlueStacks recovery in local Preferences and
  the server publishes a fresh detector-authorized request boundary. An
  incompatible API,
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

Server revision 39 retains the revision-30 `better_control_model_v1` and
`save_backed_setup_capture_v1` for additive compatibility and advertises
`better_control_model_v2`, `save_backed_setup_capture_v2`, and
`runtime_control_acknowledgements_v1`. Revision 38 additionally advertises
`strategy_aware_attach_v1`; revision 39 adds `bluestacks_maintenance_v1`.
Revision 41 adds `bluestacks_maintenance_v2`,
`bluestacks_operator_restart_v1`, and
`bluestacks_listener_lifetime_telemetry_v1`.
The additive `control_model` status object keeps
five dimensions independent:

| Dimension | Values and authority |
| --- | --- |
| Process lifecycle | `stopped`, `live`, or `unavailable`; only Start/Stop Automation changes it |
| Action authority | requested directive, runtime acknowledgement, and effective `paused`, `enabled`, `pending`, `stopped`, `unknown`, or `unavailable` |
| Observed game | fresh/stale/unavailable evidence classed as Home New Battle, Home Resume Battle, active battle, Game Over, Tournament Results, or unknown |
| Strategy scope | startup default, active-battle Strategy, pending next-boundary Strategy, and the active run's degradation status/reasons |
| When this battle ends | continue automatically, wait, or return/stay Home; `NEXT_BATTLE`, `WAIT`, and `HOME` remain compatibility values only |

The status also carries exact workflow evidence, durable battle/manual-control
ledgers, and per-action `available`, stable `code`, and operator-facing
`reason`. A client disables unavailable actions but the server independently
rechecks every request. Missing or stale evidence, a wrong runtime/process
owner or target, a changed target generation or control-operation identity,
and a mismatched visible state all fail closed. Activity scope remains
observational and available for logging, but its process-local token may rotate
without rejecting a lifecycle operation. A fresh authority heartbeat cannot
renew the nested game observation: both timestamps must remain inside their
freshness windows. Malformed control JSON makes every Better Control Model
action unavailable with `control_invalid`.

State, terminal-policy, game-speed, ADB-target, and Strategy directives carry
separate request IDs. The runtime records a receipt only after it applies the
exact request and publishes all current receipts in the same atomically
replaced runtime-owned file as action authority. Its envelope binds
`runtime_id`, PID, ADB target, and positive target generation to the active held
target lock. Status exposes receipts only while that complete owner and
freshness binding matches; a former process, recycled PID, former target
generation, rotated log, or stale snapshot cannot acknowledge a current
request. At startup the runtime atomically adds missing IDs to legacy fields
and materializes the already-established implicit state, mode, and speed
defaults, without requiring a display-refresh control request.
An optional schema-1 `emulator_location` is coupled to the ADB directive's same
request ID. It is acknowledged only after the declared-host callback completes;
an unchanged target string does not short-circuit that callback.

Status considers a receipt current only when both its value and request ID
match the current directive. A same-value replacement therefore reports
`pending` until the runtime replaces that field's receipt with the new exact
ID. Existing `[CTRL]` action-log messages remain chronological audit evidence;
neither current acknowledgement nor action authority is reconstructed from a
bounded log tail, timestamps, observations, handler activity, or an allowed
authority flag.

The runtime also authors `control_model.strategy_scope` with the startup
default, active-battle Strategy, pending next-boundary Strategy, optional
pending active-battle adoption, current Strategy request ID, and the active
run's merged degradation sources, checks, reasons, and details. The native
client uses that scope for current/next/startup/degraded presentation whenever
`better_control_model_v2` is advertised. It reconstructs the older
acknowledgement-based presentation only when that capability is genuinely
absent; a missing or contradictory compatibility acknowledgement cannot
override an authoritative scope.

### Command and transition matrix

| Process | Effective authority | Fresh observed game | Explicit request | Result |
| --- | --- | --- | --- | --- |
| Stopped | unavailable/stopped | any | Start Automation | launch service Paused; await observation and battle intent |
| Live | paused | Home New Battle | Start Battle | `requested` → `awaiting_enable`; explicit Enable revalidates and acknowledges normal new-run gates |
| Live | enabled | Home New Battle | Start Battle | revalidate and acknowledge normal new-run gates |
| Live | enabled | Home New or Home Resume with no exact immediate workflow grant | terminal-policy change, Enable, Strategy selection, or no additional request | observe and claim only a freshly visible five-gem Home reward through the typed `home_ad_gem` collector; do not serialize ordinary Home preflight, run configuration setup, claim validation, recover Home, dispatch a battle control, or run any other collector |
| Live | enabled | Home New Battle with a terminal-bound continuation | no new request | revalidate terminal-time request IDs, runtime, target generation, operation, and New Battle control; force inactive-save proof, dispatch once, then force-bind the successor ID at stable `RUNNING` |
| Live | enabled | verified Home control was tapped | acknowledged Start or ready resumable Attach | record `action_dispatched`; keep unrelated automation suppressed until the same battle is adopted, a definitive mismatch interrupts, or the 20-second launch window fails |
| Live | paused | Home Resume Battle or active battle | Attach to Battle | `requested` → `awaiting_enable`; explicit Enable enters `validating_save` without adopting the battle |
| Live | enabled | Home Resume Battle or active battle | Attach to Battle | freeze the accepted Strategy definition; active battle forces one stable exact-target save; Home Resume forces before the tap and again at first stable Running; require the active-round ID before adopting first/same/later identity, then classify as intentional No Strategy observer, compatible exact Strategy, or incompatible/unprovable degraded observer; never repair the attached battle |
| Live | either | Game Over, Tournament Results, unknown, stale, or mismatched evidence | Start Battle or Attach to Battle | reject as unavailable/mismatched; never substitute the other workflow |
| Live | enabled or paused | any fresh exact state | Take Manual Control | atomically request indefinite Pause; become `active` only after runtime acknowledgement |
| Live | paused and manual control `active` | Home New, Home Resume, active battle, or Game Over with exact runtime/target binding | Return Control | remain Paused; record visual observation only; await explicit Enable |
| Live | paused and manual control `active` | Tournament Results, unknown, or incomplete exact binding | Return Control | unavailable; no save-backed Return route is advertised or substituted |
| Live | paused, Return requested | refreshed observation | Enable | enter input-blocking `reconciling`; force current identity (or consume a bound natural Game Over save), using the two-proof pre-tap/first-Running sequence at Home Resume; classify same/later/inactive, then use supported configuration/report UI only after identity succeeds |
| Live | paused, Return `awaiting_enable`, terminal semantics unavailable | fresh exact Home New Battle | Enable | permit the ordinary save-first Home reconciliation; the unavailable terminal component remains unavailable and grants no terminal input |
| Live | paused, Return `awaiting_enable`, terminal semantics unavailable | any other boundary | Enable | retain `awaiting_enable`; send no UI or lifecycle input |
| Live | reconciling Return | source restoration, owner, target, canonical identity, or authority binding is lost after lifecycle input | no additional request | persist Automation Paused and terminalize that Return as failed/interrupted; do not repeat lifecycle input or open UI from an unsafe boundary |
| Live | enabled, adopted active battle | active battle | apply selected Strategy to this battle | adopt only after explicit selection; preserve battle identity and defer new-run/Home-only gates; Surrender remains unauthorized |
| Live | enabled, adopted active battle | recoverable configuration mismatch | no additional request | record exact degraded evidence and continue the battle; do not create a Strategy Gate, Pause, or Surrender permission |
| Live | enabled | Home New, Home Resume, or active battle with exact binding | Capture current setup as… | force a new save, present captured and unresolved fields for review, then save a new inactive Module preset or Strategy draft without selecting, queueing, publishing, or applying it |
| Live | capture owns a forced refresh | compatible exact/forward save revision | no additional request | use only the resolved mapping's explicit compatibility allowlist; preserve every other setup field as unresolved |
| Live | capture owns a forced refresh | source restored, but mapping/projection/acquisition is unavailable or round identity is incomplete | no additional request | report `unavailable`, open no configuration UI, and preserve the prior action-authority state |
| Live | capture owns a forced refresh | source restored, but fresh active/resumable evidence contradicts the requested battle identity | no additional request | report `failed`, release capture ownership, and preserve the prior action-authority state |
| Live | capture owns a forced refresh | fresh Home New evidence contradicts the requested boundary, or an attempted lifecycle transition cannot prove source restoration | no additional request | report `failed` and persist Automation Paused because the safe input source is unproven |
| Live | capture owns or completed a forced refresh | ready/terminal ledger write fails | no additional request | retain the exact process-local result and retry only its atomic receipt without changing action authority or serializing again |
| Live | capture has a terminal result | `saved`, `cancelled`, `unavailable`, `interrupted`, or `failed` | reopen Capture | inspect the prior result only; a new serialization requires the separate explicit **Try capture again** action |
| Live | enabled | Game Over after a configuration-degraded strategy battle | Continue was already selected for this terminal | snapshot the degradation, collect terminal data best effort, return Home, apply any pending Strategy, run its ordinary bounded setup, and consume one exact continuation; failed Home navigation retries and exhausted setup launches degraded without changing global authority |
| Live | enabled | any other Game Over | selected future terminal policy | collect terminal data best effort, then follow Retry/Home; if the route fails, retain it for a fresh-evidence retry without changing authority |
| Live | enabled | Tournament Results | selected future terminal policy | `WAIT` retains the screen; Continue/Home first capture the result and use the verified dismissal route; failure retries from fresh evidence without changing authority; only Continue already selected for that terminal boundary can carry one exact launch through verified New Battle Home |
| Live/stopped | already satisfied | any | repeated Pause, Enable, Start Automation, Stop Automation, terminal policy, or Take Manual Control where defined | return a visible no-op instead of fabricating a transition |

An intent requested under Pause is pending, not acknowledged action authority.
If the runtime, target, workflow/control identity, or observed boundary changes before
acknowledgement, the request becomes `rejected` or `interrupted`. Stop and a new
process boundary interrupt unfinished workflows. Home alone never enables or
pauses input.

At Tournament Results, `WAIT` is satisfied by retaining the screen until its
bounded idle deadline. Continue automatically and Home first preserve the result, then use the
verified OK-to-Home dismissal owner. A failed dismissal retains the selected
future policy and retries from fresh terminal evidence without changing action
authority. Continue still does not make the policy control an immediate battle
command. If Continue was already selected when this exact terminal boundary
was handled, successful dismissal may freeze one process-local continuation
claim for the next verified New Battle Home. Selecting Continue after a
retained result can dismiss that result, but it does not retroactively create a
Home launch claim.

Managed Home launch authority is deliberately narrower than terminal policy.
An ordinary healthy Game Over under Continue uses its direct Retry control. A
configuration-degraded strategy battle instead returns Home, rearms the next
profile's normal setup, and attempts that bounded repair before launch; repair
exhaustion flags the new failure but does not suppress continued automation. A
route that must pass through Home—degraded-battle repair, No Strategy post-run
collection, an explicitly authorized configuration-repair return, or
Tournament Results dismissal—may carry a one-shot claim created from the exact
terminal observation. The claim is bound to runtime/PID, ADB target and
generation, terminal active-round identity, and the state and mode request identities in force
at that terminal. It survives only its owned Home work, requires fresh
`NEW_BATTLE`, and is consumed only after a verified dispatch. Policy or
authority request changes, manual/workflow supersession, Resume Battle,
owner/target/battle-identity change, process replacement, or unexpected manual activity
discard it. Being at Home, selecting a Strategy, or changing **When this battle
ends** never creates one.

`WAIT` and `HOME` additionally create a durable bounded-idle hold only after a
fresh supported terminal or Home New observation. The hold is bound to the
exact state and mode request identities. At 30 minutes it routes terminal
screens through Home and atomically creates an ordinary Start Battle workflow
for `farm_t19_ad_assist`; that workflow owns the same save, setup, tier, and
first-Running identity gates as an operator Start. An explicit state, Strategy,
manual-control, or battle-workflow write consumes the hold and resets the future
policy to Continue; an explicit policy write replaces it with that newer
policy. A timed Pause that expires during an active battle resumes that same
battle instead of switching Strategy; if it expires at Home New, it creates
the same fallback Start workflow. Indefinite safety and manual-control pauses
never expire.

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
missing a valid active/inactive identity, no UI route can substitute and
battle-bound work stays blocked. Once identity is established, an unsupported
or unprojectable configuration/report component may use its complete supported
UI route and write an exact-bound reconciliation receipt.
A terminal component marked `unavailable` normally blocks Enable. The one safe
exception is a fresh exact Home `NEW_BATTLE` observation while Return remains
`awaiting_enable`: Home save-first reconciliation can establish its own source
and boundary authority without pretending that the missing terminal component
became available. The runtime rechecks that exact Home boundary immediately
before entering reconciliation; replacement, Resume, active, terminal, stale,
or unknown evidence keeps the hold and sends no input.
A loss of restoration, owner, target, canonical identity, or action authority terminates
the exact workflow and leaves Automation Paused rather than opening UI from
cached data. Home New terminalizes that unsafe outcome once, so later
heartbeats do not background the game again. A trusted mapped mismatch remains
distinct from unusable save data but is recoverable. Active/resumable Return
completes with exact degraded evidence. Home New repairs it immediately at the
already-safe boundary and, if bounded repair exhausts, completes with the exact
failed check and reason. Neither outcome restores Pause, retains Return capture
authority, or waits for another Enable.
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
listeners. A PC may also reuse a former PC's Linux port after the former
reverse forward releases it. **System > Connections > Use this PC's emulator**
submits the active forward's actual Linux and Windows ports, the client's
stable local host ID and name, and any exact listener process identity the
client can inspect. The Linux runtime treats that declaration as a host
handoff even when its `localhost:<port>` text is unchanged: acknowledged
indefinite Pause is required, the current endpoint must produce a supported
fresh frame, and success advances the target generation before acknowledgement.
The client never terminates or adopts another PC's tunnel. If that tunnel still
owns the requested Linux listener, the new tunnel remains in visible conflict
until the operator stops or reconfigures the former forward.

The independent process boundary keeps an ADB bind conflict or
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
| `GET` | `/api/v1/status` | Server revision/capabilities, Better Control Model dimensions/workflows, control intent, acknowledgement, current-run identity, current save-backed Perks, persistent save-mapping review status, latest observation, structured Strategy Action Gate, and runtime evidence |
| `POST` | `/api/v1/control` | Allowlisted control mutation |
| `POST` | `/api/v1/interactive-development-lease` | Request, heartbeat, or release the one cooperative development lease; never dispatch device input |
| `POST` | `/api/v1/host-maintenance` | Create or advance the typed BlueStacks restart handshake; automatic creation requires exact detector lifetime evidence, operator creation bypasses only that decision, both require fresh runtime authority and a durable exact Windows target, and acknowledgement/completion prove the old and replacement identities |
| `POST` | `/api/v1/process` | Start/stop the fixed systemd automation unit independently of battle intent, save/queue/adopt a bundled or published custom strategy, configure/safely hand off its ADB port, or bind the active forward to a typed Windows emulator host |
| `POST` | `/api/v1/host-performance` | Bounded, idempotent batches of native Windows host/BlueStacks performance aggregates |
| `GET` | `/api/v1/strategy-profiles` | Bundled/custom profile summaries plus the allowlisted Farm policy and preset catalogs |
| `POST` | `/api/v1/strategy-profiles` | Validate a constrained Farm draft or atomically publish its source and generated plan |
| `GET` | `/api/v1/strategy-authoring` | Registry metadata, separate Base/Strategy catalogs, authoritative merged Module preset details, editable source, effective resolution/provenance, compatible Base revisions, capabilities, and catalog errors |
| `GET` | `/api/v1/strategy-authoring/history` | Newest-first immutable custom-Strategy lineage and revision summaries, including retired lineages, without expanded plans |
| `GET` | `/api/v1/strategy-authoring/history/{id}` | One custom Strategy lineage and its ordered revision summaries |
| `GET` | `/api/v1/strategy-authoring/history/{id}/{version}` | One retained revision's review-safe source, Base snapshot, resolution, fingerprints, audit identity, and validation state without its generated plan |
| `POST` | `/api/v1/strategy-authoring` | Validate or publish Base/Strategy source, preview a Base pin, materialize a catalog-bound normalized preset copy, create an immutable custom Module preset, compare retained revisions, or review/confirm restore-as-new, without runtime-control mutation |
| `GET` | `/api/v1/setup-capture` | Current runtime-issued capture status, availability, inactive captured-draft catalog, Module presets, and comparison Bases |
| `POST` | `/api/v1/setup-capture` | Request a new forced-save capture, review a fingerprinted captured-versus-Base difference, save through the existing Module/Strategy owner, or cancel; never activate or publish |
| `GET` | `/api/v1/setup-capture/drafts/{id}` | Reopen one immutable captured Strategy source in the ordinary authoring editor without selecting or activating it |
| `GET` | `/api/v1/save-mapping-integration` | Durable candidates, machine-verification proof, automatic-integration readiness, and exact queued/recovery state; never mutates a repository |
| `POST` | `/api/v1/save-mapping-integration` | Review, dismiss, or integrate one server-generated exact candidate; integration owns its internal stage, guarded fast-forward, and verified non-forcing publication but never controls services, runtime authority, or device input |
| `GET` | `/api/v1/battles?limit=N` | Newest Battle and Tournament summaries |
| `GET` | `/api/v1/battles/{battle_id}` | One full structured battle record |
| `GET` | `/api/v1/activity?limit=N&levels=ERROR,WARN&scope=current_run&after=CURSOR` | Recent structured action-log entries, optionally filtered by level, explicit run scope, and opaque clear-view cursor |

### Current battle Perk status

Server revision 32 advertises `current_battle_perks_v1` and adds the
`current_battle_perks` status object. The runtime's existing Perk timeline
owner writes a compact presentation beside its atomic same-run checkpoint only
after the shared save monitor accepts a complete exact prefix. It collapses
that prefix to one row per semantic Perk, records the current level and most
recent saved selection wave, and orders rows by most recent selection. The
object also retains the checkpoint capture time, saved wave, total pick count,
and unique count so the client never presents a passive save as newer than it
is.

The adapter accepts the presentation only when its checkpoint schema is
supported and its canonical battle identity matches the runtime's current
force-bound identity. A missing checkpoint reports `awaiting_save_checkpoint`;
a missing identity or malformed projection reports `unavailable`; and every
such result contains an empty item list. Activity scope may group the display
but cannot hide or revive a battle. This is a read-only projection: the API performs no
save acquisition, serialization, panel navigation, device input, or action-
authority decision.

### Save-mapping review status

Server revision 40 advertises `save_mapping_review_status_v2` and
`confirmed_local_mapping_status_v2`. The
`confirmed_local_mappings` status object combines durable unmapped-value
candidate receipts with exact-version local Module confirmations. Browser and
native clients show a persistent nonmodal banner for review, more-evidence,
local-active, authority/mirror-pending, reconfirmation, ambiguity, or conflict
states. It also preserves direct-integration recovery, production-promotion,
and fresh-decode checkpoints even when the same candidate already has a local
confirmation. Integrated and explicitly revoked records disappear from the
banner.

The banner is diagnostic. It never blocks startup, changes Automation state,
suppresses a UI check, or grants integration/revoke authority. A missing or
unreadable status contract is shown as a compatibility/error state; canonical
save mappings and their existing UI fallbacks remain runtime authority.

Server revision 42 introduced `save_mapping_staged_candidate_v1`. The banner's
**Review mappings…** action and the native **Tools > Save mapping
integration…** item open the same workflow. Candidate is the only selection,
and requests cannot carry a filesystem path, ref, target, patch operation,
mapping value, commit message, or Git identity.

Server revision 44 added `save_mapping_candidate_disposition_v1`. Every
selected observation has an explicit path forward. An ordinary safely
generated proposal enables **Review exact proposal**; an ordinary unreviewed
observation may instead use **Dismiss observation…**. That exact-shape request
carries only `operation=dismiss` and the candidate record ID. The server
appends an idempotent disposition, preserves the original receipt, and returns
the disposition event identity with `evidence_preserved=true`. Any
nonreviewable item displays its reason, next action, and a selectable agent
request; the client tells the operator to involve an agent but never launches
or authorizes one.

Server revision 45 adds `save_mapping_machine_verification_v1` and
`save_mapping_automatic_promotion_v1`. The read-only **Automatic integration
readiness** panel shows current production, the internal transaction, and any
blocker. A deterministic `battle_history_killed_by_id` candidate with an exact
pre-mutation raw save value, matching terminal Game Over or Tournament Results
semantic value, complete causal fingerprints, compatible revision ownership,
and no conflict is machine-verified. Its exact proof remains visible, but it
needs neither review nor confirmation and cannot be dismissed. Anything short
of that proof follows the ordinary review, dismissal, or agent route.
Actions that do not apply to the selected state are hidden rather than left as
unexplained disabled controls; every nonreviewable state therefore presents its
automatic status, dismissal, or copyable agent path directly.

Review is read-only and binds the candidate receipt, proposal, exact target
before/after hashes and modes, prospective canonical mapping fingerprint, and
standardized commit contract. `reviewed_base_commit` remains visible for audit,
but unrelated content and the whole `main` object are intentionally excluded
from the reviewed proposal fingerprint. On **Integrate reviewed mapping…**, or
automatically for a machine-verified candidate, the server recomputes the
proposal against current `main`; all reviewed target and evidence inputs must
still match.

The server builds one standardized child of current `main` with a private Git
index, verifies the exact allowlisted path set, blobs, modes, message, and
provenance, then atomically creates the fixed private candidate ref. That ref
is an internal crash-safe boundary, not a user-visible completed outcome. Its
consumer acquires the same global promotion-owner ref as ordinary outcomes,
creates a deterministic rollback tag, fast-forwards clean production `main`
under the mapping-file write lock, re-verifies the candidate and canonical
mapping set, publishes that exact commit to `origin/main` without force, and
verifies remote ancestry. It never publishes a larger enclosing outcome.

Both clients accept a completed result only when it proves
`disposition=promoted`, `promoted=true`, and `published=true`. A recoverable
blocker instead returns `disposition=promotion_queued`, exact durable local and
published state, bounded automatic retry, a concrete reason, and an agent-ready
recovery request. A remotely published transaction that has not
compare-released its exact global owner is explicitly
`promotion_cleanup_pending`; the service reconciliation worker consumes that
state rather than waiting for a decode. It attempts all queued work immediately
at startup and then with bounded backoff. GUI refresh is observational; it is
not the missing consumer.

The durable transaction records generation, private ref creation, local
promotion, publication, and fresh-decode validation. Response loss or a crash
resumes the same commit, rollback tag, promotion owner, and publication
boundary. An unrelated `main` advance permits automatic restaging only when
the exact target inputs remain unchanged. A Git crash lock, moved ref, changed
target, malformed journal, or unprovable state fails closed with the agent
request; it is never reset, force-pushed, or turned into a duplicate mapping
commit.

The persistent banner reports automatic integration or publication pending
while recovery is active, then **Deployed save mapping awaiting fresh
validation**. Runtime mapping loads use a shared file lock and signature-keyed
cache, so the next acquisition observes the complete promoted mapping set
without a service restart. A privacy-safe receipt clears the final checkpoint
only when remote publication is verified and a fresh acquisition began under a
production commit containing the mapping with matching canonical identity and
fingerprint. Receipt work runs after the outer ADB/mutation lifecycle has
released. Failure stays diagnostic and cannot degrade a valid save or change
automation.

### Structured Strategy Action Gate status

This field is a compatibility surface. Current runtimes do not activate it for
recoverable configuration, validation, evidence, repair, or reporting failures;
they migrate a legacy session-preflight gate to degraded evidence instead.

`GET /api/v1/status` exposes `strategy_action_gate` separately from
`control.state`, state acknowledgement, and the latest observation. The object
reports availability, active/inactive and stale state, age, owner match,
strategy and canonical battle identity, source/phase, failed check IDs, operator reason,
activation/update times, Pause/Stop context, active exclusive holds, the four
typed authority decisions, any optional collector allowlist declared by a
hold, currently allowed auxiliary collectors, and any exclusive auxiliary
route.

The adapter reads only the runtime-owned atomic snapshot. It rejects malformed
or unsupported schemas, inactive publishers, expired timestamps, and PID/ADB
owners that do not match an active runtime lock. Revision-37 acknowledgement
and authoritative Strategy-scope projections additionally require the exact
runtime ID and target generation recorded in that lock. Missing or stale
evidence is reported as unavailable/stale and cannot be promoted into action
authority.
Warning text in the action log is never parsed as gate state. Adding this
field is backward compatible: earlier clients ignore it and retain every older
endpoint and capability. `home_ad_gem` is an additive collector value and a
conformance repair to the existing `better_control_model_v2` capability; it
adds no command, endpoint, or client-side authority inference.

### Interactive development lease status

Server revision 26 advertises `interactive_development_lease_v1`; runtimes with
the additive `interactive_development_owned_battle_v1` capability also accept
one explicit preclaim. The `POST /api/v1/interactive-development-lease`
operation model is:

```json
{"operation": "request", "owner_label": "bounded task label"}
{"operation": "request", "owner_label": "owned test", "owned_battle_start": true}
{"operation": "heartbeat", "lease_id": "ordinary coordination ID"}
{"operation": "release", "lease_id": "ordinary coordination ID"}
```

There is no client-supplied runtime, PID, target, timeout, source fingerprint,
secret, action, or command. `owned_battle_start` is only a boolean request for
the server-derived current binding. The server derives the binding from a fresh
runtime-owned authority snapshot that matches the active OS lock. A live
conflict returns HTTP 409 with code `busy`; expired, terminal, or wrong-ID
heartbeats and releases are rejected, and a heartbeat additionally requires
fresh matching runtime ownership. Heartbeats extend the fixed 120-second expiry
without adding an action-log entry.

The owned-battle variant is accepted only from fresh exact Home
`NEW_BATTLE`, force-proven inactive evidence, and positive target generation.
The preclaim is provisional: activity scope is irrelevant, and terminal
cleanup is authorized only if the launched run later has an exact force-bound
`ActiveRoundIdentity` matching the terminal. Because `external_development`
deliberately forbids runtime lifecycle input, a lease that remains held for the
whole run normally declines automatic cleanup rather than guessing identity.
It cannot collect a representative battle, Retry, claim a pre-existing or
Tournament run, or survive runtime/PID, target-generation, canonical identity,
control, or boundary replacement.

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
no delete or arbitrary-path operation on the older profile endpoint.
Publication and control remain separate API operations. In the native workflow,
a successful Strategy publication during an active process automatically
triggers the existing process API's normal next-boundary request; it never
triggers active-battle adoption. When stopped, the client selects it for Start
without persisting a different startup default. If the active request fails,
the publication remains committed and the selected Strategy remains available
for Retry. Other clients may still select or apply a published profile
explicitly through the same process API.

## Sparse strategy authoring

Server revision 31 preserves `strategy_authoring_v1`,
`strategy_authoring_specialized_editors_v1`,
`strategy_authoring_profile_lifecycle_v1`, `strategy_action_gate_v1`, and every
older capability, retains `strategy_revision_history_v1`, and advertises
`strategy_authoring_preset_local_copy_v1`. Revision 25 added
`managed_custom_module_presets_v1` after revision 24 added
`strategy_authoring_local_loadout_editors_v1`. The additive
`/api/v1/strategy-authoring` endpoint implements the sparse Base/Strategy model
without changing `/api/v1/strategy-profiles`, `strategy_profile_catalog_v1`, or
`strategy_profile_editor_v2`. Pre-authoring native clients therefore keep using
their existing latest-only facade. The revision-23 authoring client retains its exact
preset-only metadata path against the newer service. Revision-24 option and
local-editor wire shapes remain unchanged. The revision-31 client requires the
retained authoring, Strategy Gate, history, local-loadout, and managed Module
preset capabilities plus preset-local-copy materialization and fails
compatibility clearly against an older resident service.

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
Each preset/local entry also carries the fingerprint of the exact preset
catalog snapshot shown to the client. Revision-31 clients use that token only
for the explicit server materialization operation; older clients ignore it.
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

For Modules, Target Priority, and Orb Distance, **Edit a copy...** submits the
exact selected preset and displayed catalog fingerprint to
`materialize_loadout_preset`. Linux compares one current catalog snapshot and
uses its ordinary definition-snapshot resolver and normalizer to return the
complete local value. WPF validates the response identity and local-editor
shape before atomically replacing the dormant local form and switching the row
to **Profile-local definition**. A meaningful dormant local draft produces an
explicit replace/retain/cancel prompt; cancel and every stale, unknown,
unsupported, interrupted, or invalid response preserve both forms. Read-only
bundled Strategies cannot edit a copy, while editable clones and custom
Strategies can. Copying changes only the open draft: it does not publish,
select, activate, queue, or apply a Strategy or preset.

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
**Duplicate preset...** creates a new immutable exact-copy preset from either
origin, while **Save as preset...** submits the current metadata-driven local
definition. Both are save-as-new and remain distinct from **Edit a copy...**:
no overwrite, rename, deletion, or retirement exists. A successful create
refreshes options incrementally, preserves every retained selection object,
and explicitly moves the current row to the new preset while leaving Validate
→ Review → Publish pending. A failed create preserves the draft and
selections. The controls are hidden without the managed capability. No Target
Priority or Orb Distance custom-preset catalog is introduced.

POST accepts `validate_base`, `publish_base`, `validate_strategy`,
`publish_strategy`, `preview_rebase`, `retire_strategy`,
`compare_strategy_revision`, `preview_restore_strategy`, and
`publish_restore_strategy`, plus `materialize_loadout_preset` and
`create_module_preset`. Materialization accepts exactly `setting_id`, `preset`,
and `expected_catalog_fingerprint`; stale catalog state is HTTP 409, while an
unknown preset or normalization rejection is HTTP 400. Success returns the
normalized definition with `published: false` and never writes or changes
control state. The creation payload
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
is present. The authoring endpoint keeps the Strategy ID and publishes a new
version without changing control state. The native workflow then selects that
Strategy and separately queues its latest definition for the next boundary; it
does not switch the current battle.

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
refreshes history/latest catalogs only after a successful restore. The restore
endpoint never mutates history, restarts automation, changes Pause, or changes
a control directive. After success, the native workflow selects the restored
Strategy and separately queues its newly published latest definition for the
next boundary without switching the current battle.

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

The human-readable operator `STATUS` summary remains intentionally periodic so
the durable log is not flooded. It contains state, wave, Coins/min, and speed;
its paired `[STATUS_DETAIL]` diagnostic retains menu, secondary-state, and
overlay evidence. Live status does not wait for that log cadence: each
main-loop frame accepted at the canonical observation boundary publishes a
fresh structured observation in the atomic runtime-owned snapshot. The Linux
adapter still accepts both the paired and earlier all-in-one `STATUS` formats
for retained history. The GUI presents only the latest summary and a prior meaningful
transition outside the Operational activity list while retaining complete
status history in `Status only` and `All levels`.

Control acknowledgements are not part of that transitional log-derived view.
The action log retains their semantic audit messages, but revision 37 obtains
current state, terminal-policy, speed, ADB, and Strategy receipts only from the
fresh exact-owner atomic runtime channel. Log size, bounded-tail position, and
rotation therefore cannot change Action Authority, acknowledgement indicators,
setup-capture availability, or paused ADB-handoff eligibility.

The native client's default `Current run` scope uses the atomic
`logs/activity_scope.json` ledger. Automation startup creates it only when no
valid scope exists and otherwise reuses it, while verified Home `NEW_BATTLE`
preflight may replace it for presentation. It does not infer a battle from
human-readable log messages, and its value is never compared with canonical
battle identity. Terminal History metadata may be attached best effort for
report grouping; an unavailable ledger or a rotation cannot permit, reject, or
interrupt runtime work. This is runtime-owned display metadata and does not
change the activity API or native-client compatibility revision.
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

Process attribution is dormant during ordinary load. When host CPU remains at
or above `70%`, memory use remains at or above `95%`, or available physical
memory remains at or below `1 GiB`, for 30 seconds, the existing ten-second
process-discovery pass also reads CPU time, working set, and private bytes for
accessible non-BlueStacks processes other than the control-surface client. The
first active pass establishes CPU baselines while still supplying memory
attribution. Collection continues until every pressure condition has remained
clear for two minutes. Each pass retains
the union of the four highest CPU consumers and four largest working-set
consumers, bounded to eight distinct PID/name pairs. It records neither command
lines nor window titles and does not add another process scan or launch a
diagnostic provider.

Host and BlueStacks GPU utilization use the busiest-engine convention: values
from processes sharing a physical engine are combined, then the busiest engine
is reported and capped at 100%. Adapter-level dedicated/shared usage represents
host GPU memory; process-level usage supplies BlueStacks and competitor
attribution. Raw samples retain at most eight non-BlueStacks competitors.
Each ten-second aggregate publishes at most five, ranked first by maximum GPU
use and then GPU memory, with PID, process name, observation count, average and
maximum utilization, and maximum dedicated/shared memory. No per-process or
per-engine sample is published individually.

An aggregate produced during active process attribution publishes at most
eight non-BlueStacks PID/name entries with observation count, average/maximum
host-normalized CPU, and maximum working-set/private bytes. A dedicated process
count and scan-duration metric makes the added collection cost measurable. The
GUI separately derives **Other Windows CPU** by subtracting measured
BlueStacks and control-surface CPU from total host CPU; this is an explicitly
unattributed residual, not a sum of the bounded process list. The compact top
CPU and memory fields group retained PID entries by process name, while the
tooltip preserves each PID separately.

The client retains 120 raw samples in memory and reduces each ten-sample window
to averages and extrema. On the existing ten-sample process-discovery pass it
also resolves the configured BlueStacks ADB listener to an exact host name,
port, `HD-Player.exe` path, instance, PID, and process start time. An ADB-port,
run-identity, configured-target, or exact-listener transition closes the current
window early rather than mixing correlations. A scheduler, sleep, or wall-clock
discontinuity greater than five seconds also closes the partial window before
the next sample, keeping the downtime explicit and preventing a single
aggregate from spanning the server's five-minute validation ceiling. Failure
or multi-instance ambiguity leaves this optional listener identity explicitly
unbound while ordinary host metrics continue. Each aggregate carries a
stable locally generated host ID, Windows host name, client session/sequence,
UTC window, logical-processor count, ADB port, and the run ID observed through
the status API. A run ID expires from new samples when status has not refreshed
for 15 seconds; outage telemetry remains available without being falsely
assigned to a later run.

Sampling can be stopped and started only by the local native client. Stopping
closes and persists the current partial aggregate before the sampler waits;
the uploader remains active so previously queued evidence can continue
reconnecting and publishing. While disabled, the health and queue presentation
say **Sampling off**, never **Buffering**; a remaining backlog separately says
that the independent uploader is draining it, or that it remains local when
upload is unavailable. Starting keeps the same host/session identity and
sequence, while the UTC window timestamps leave the intentional sampling gap
explicit. The enabled state is stored in the native client's local settings
and does not add Linux API control authority.

Aggregates first enter
`%LOCALAPPDATA%\TheTower\host-performance-pending.jsonl`. The bounded spool
keeps the newest 24 hours at the nominal ten-second cadence and reports any
drops in the GUI. Upload resumes in bounded batches after an API or tunnel
outage. A schema rejection identifies the exact aggregate index. The client
first appends that aggregate and the server reason to the durable
`host-performance-rejected.jsonl` diagnostic spool, then atomically removes
only that UUID from the pending spool so valid neighbors can retry. A failure
to preserve or checkpoint the rejected aggregate leaves it pending; an
unindexed request rejection never authorizes removal. The compact GUI queue
state reports live sampling/backlog state, current upload errors, and capacity
drops; a successful acknowledgement clears the current upload error. Retained
schema-rejection history and its latest reason remain available in the Host
Health tooltip as diagnostic context rather than a current-failure indicator.
The diagnostic spool retains the newest 1,024 unique rejected aggregates so a
systematic producer fault cannot grow local storage without bound. Aggregate
UUIDs are primary keys in
`logs/host_performance.sqlite3`, so retrying after a lost response is safe. The
Linux store also records the server's current run at ingest as separate
diagnostic context, keeps the sample-time run authoritative, and prunes records
after 30 days by default. Server revision 12 advertises capability
`host_performance_telemetry_v1`; server revision 13 adds
`host_performance_gpu_v1`; server revision 36 adds the optional
`process_attribution` aggregate field and capability
`host_performance_process_attribution_v1`. Older native clients remain valid
publishers because the new field is optional.
Server revision 41 adds `bluestacks_listener_lifetime_telemetry_v1`. Linux
selects history from the newest current-run row and admits earlier aggregates
across GUI sampler sessions only when the stable local host ID, Linux ADB
target, and every exact listener-identity field remain equal. A missing legacy
or currently unbound listener is insufficient evidence; it never permits
cross-session stitching. This keeps a GUI close/reopen from erasing the aging
trend while a BlueStacks process remains alive, but resets the trend on any
listener replacement, PID reuse with a new start time, target edit, or active
multi-instance ambiguity.
Server revision 46 adds `emulator_host_selection_v1`. Once a Windows host is
explicitly selected, current listener-lifetime queries are constrained to that
stable host ID; a former client still publishing the same run and Linux port
cannot become the selected host's degradation evidence.

The no-frame-telemetry target is below 0.5% average host CPU. Aggregate fields
include control-surface CPU and sampling duration so the Windows deployment can
verify that budget. GPU collection also records its own sampling duration.
Threshold-triggered process attribution records its scan duration separately
and must be included in clean and contended client profiling.
Temperature and clock telemetry are not included because Windows does not
provide them through the same vendor-neutral counters. Continuous frame timing
is not a planned control-surface telemetry feature. If a specific performance
anomaly cannot be resolved from the retained counters, collect one bounded,
opt-in diagnostic trace for that issue rather than adding a permanent provider,
frame spool, or dashboard surface.

### Automatic BlueStacks degradation recovery

Server revision 39 adds capability `bluestacks_maintenance_v1`; revision 41
adds the exact-target `bluestacks_maintenance_v2`, operator command
`bluestacks_operator_restart_v1`, and listener-lifetime telemetry capability.
Revision 41 supersedes and no longer advertises the v1 request contract, so a
server-first rollout makes an older native client fail its compatibility check
instead of submitting an unbound recovery request.
Revision 43 adds `bluestacks_maintenance_policy_v1` and three independently
observable automatic trigger lanes. Automatic creation remains disabled by
default behind one master option. Preferences retain separate child options
for the preventive handle ceiling, severe in-run loss, completed-run
confirmation, and deferral of the preventive lane during external contention.
The two new proactive lanes default off, completed-run confirmation retains the
prior child behavior, and contention deferral defaults on.
Changing any option affects only new requests; an accepted durable request is
always reconciled. Before enabling it—or using the operator restart—the operator
must verify the absolute `HD-Player.exe` path, the Windows ADB listener port,
and the instance name against a shortcut created by the installed BlueStacks
version. The client launches only
`HD-Player.exe --instance INSTANCE`; that argument form is deliberately
configurable because BlueStacks documents per-instance shortcuts but does not
publish a stable raw command-line contract.

The Linux assessment is intentionally conservative, side-effect free, and
continues while every automatic option is disabled. It exposes three lanes:

- **Preventive handle ceiling.** The recent median must remain at least 25,000
  OS handles for ten sampled minutes, remain at least 10,000 above the retained
  low-water of the exact BlueStacks listener process lifetime, and keep a
  stable nonzero process set. The low-water query crosses Windows GUI sampling
  sessions and completed battle scopes; a twelve-hour sliding baseline cannot
  teach itself that a slow leak is normal. This is a provisional maintenance
  threshold based on low restart cost and observed correlation, not a claim
  that handle count alone proves a performance bottleneck.
- **Severe in-run loss.** Periodic and Perk-requested player-save checkpoints
  may supply interval CPH, wave, and effective speed to the metric consumer
  without another save read. The independent passive scheduler observes on a
  300-second cadence without claiming save freshness. Three fresh consecutive
  intervals must each be at or below 60% of a
  conservative lower envelope built from at least six intervals across two
  completed runs on the same explicitly attributed Windows host, in the same
  Strategy, exact run configuration, save-mapping semantics, and broad
  1,000-wave band. Effective speed must remain at least
  97% of its comparable baseline and sustained handle growth must also be
  present. Missing history, a changed loadout/configuration, ordinary variance,
  or partial attribution therefore cannot trigger this lane. The current-run
  lane is disabled immediately when that battle's host timeline is partial or
  mixed, so intervals collected before and after a PC move are not treated as
  one performance regime.
- **Completed-run confirmation.** The legacy conservative detector compares
  the newest two representative completed Farm runs with the preceding three
  to five exact-configuration runs from the same explicitly attributed Windows
  host. Both candidates must be at or below 93% of baseline, their median at or
  below 90%, and effective speed at least 97% of baseline, with sustained
  handle growth. Legacy unattributed history remains usable only until any
  completed record carries host tracking; after that transition, partial and
  mixed-host records are excluded and five same-host records are required.

Recent CPU, GPU, memory, available-memory, and clock evidence is evaluated over
the current host window. Sustained load outside BlueStacks is reported as
external contention. Ambiguous or external contention always prevents the
performance-attributed lane; the default Preferences policy also defers a
preventive handle restart because that restart would not remove the competing
load. An operator may disable only that preventive deferral. Exact-lifetime
correlation preserves aging evidence across battle boundaries and Windows GUI
sessions without mixing another PC, runtime target, listener port, instance,
or BlueStacks process.
The Linux runtime port and Windows listener port are correlated independently
and need not be numerically equal. Missing exact-lifetime host
corroboration produces a recommendation only; host saturation defers recovery.
Any battle that already contains emulator-recovery provenance is excluded from
future calibration. The service caches this read-only assessment for one minute
and retains the completed-run cohort until the battle directory changes, so
five-second status polling does not repeatedly parse completed reports and
thousands of retained host windows.

When an enabled lane is ready and the shared automatic request gate is open, an
opted-in Windows client may request one restart and records the selected lane
in durable trigger provenance. A disabled ready lane remains visible as
"would trigger (disabled)." The client first proves that its freshly inspected
listener is the same exact process lifetime named by the detector. Request
creation still requires a fresh owner-matched
`RUNNING` Farm battle, exact active Strategy and canonical battle identity, Enabled
automation, no other hold, and both normal Strategy and lifecycle authority.
Only one automatic attempt is allowed per battle, and a terminal request starts
an eight-hour cooldown.

**System > Diagnostics > Restart BlueStacks…** is a separate confirmed operator
request. It bypasses only the performance decision, automatic opt-in, and
automatic cooldown/once-per-battle creation gates. Linux still requires the
same fresh, unheld, exact-owner `RUNNING` Farm battle with normal Strategy and
lifecycle authority. The confirmation names the immutable instance, path,
port, and current PID and explains the possible non-earning replay through the
old wave high-water and the End run/New Battle fallback. Multiple active
instances block host-wide automatic evidence but do not make this explicitly
targeted operator action ambiguous.

The durable request separates the two mutation owners:

1. Before Linux installs a hold, Windows submits and Linux durably binds the
   immutable executable, instance, port, host, listener PID, and listener start
   time together with the request initiator. Automatic creation additionally
   compares that identity with the detector's exact listener lifetime. Linux
   also binds runtime ID, PID, ADB target, positive target generation,
   authorizing state-request ID, and canonical battle identity. Request creation atomically
   rechecks that the same state request is still `RUNNING`. The runtime
   captures the current wave, installs the suppressive `emulator_maintenance`
   hold, and publishes a separate fresh authorization. Host acknowledgement
   atomically rechecks that exact state request is still `RUNNING`.
2. Windows reuses only that durable target, maps
   `bst.instance.INSTANCE.status.adb_port` from `bluestacks.conf`, resolves
   that loopback/any-address listener through the native TCP owner table, and
   requires exactly one process whose executable path is the configured
   `HD-Player.exe`. Path and creation-time inspection uses the exact PID with
   Windows `PROCESS_QUERY_LIMITED_INFORMATION`, `QueryFullProcessImageName`,
   and `GetProcessTimes`; it does not request module-enumeration access, which
   BlueStacks may deny even to an elevated interactive client. It posts the
   target plus host name, PID, and start time before mutation. If more than one
   configured instance has an active ADB listener, automatic recovery is
   disabled because the host-wide aging evidence is ambiguous.
3. Immediately before graceful close—and again before the force-kill fallback—
   Windows revalidates listener PID, executable path, and start time. A force
   fallback retains and terminates the already verified native process handle,
   so later PID reuse cannot retarget it. Windows starts only the configured
   instance and accepts completion only after a different exact process owns
   the listener for two consecutive polls.
4. Linux then owns ADB reconnection, The Tower launch, Welcome Back handling,
   replay suppression, and the configured new-battle fallback described in the
   [runtime architecture](runtime.md#emulator-maintenance-and-restart-replay).

Lost acknowledgement and completion responses are idempotent. After host
acknowledgement, an uncertain result retains the Linux hold and reconciles the
exact old or replacement listener on a later poll; it does not report failure
merely because the response was lost. Disabling the Preference stops creation
of new requests but does not abandon an already accepted one. A request that
never receives a Windows process acknowledgement expires after three minutes,
before host mutation, and normal runtime authority is restored. Once Windows
has acknowledged the old identity, no timeout may guess whether mutation
occurred; durable reconciliation is required. Pause before host
authorization blocks the restart; Pause after acknowledgement allows Windows
to reconcile the accepted host operation while Linux continues to block every
game input until Enabled again. Recovery target fields are locked while a
request is active. Closing the native client waits for its current coordinator
operation to reach a reconciled boundary, so the API client is not disposed
between durable acknowledgement and replacement reporting. The host sampler
also resets process/rate baselines at that restart boundary.

The Diagnostics **BlueStacks** card shows the current host-wide Windows handle
and thread counts, refreshed with process discovery. A separate detector line
shows the exact-lifetime recent median, low-water, ratio, delta, stable window
count, PID, and number of contributing GUI sessions. Coordinator progress is a
separate field so restart messages and status polling do not erase the detector
evidence. The automatic-policy line independently shows the master state, all
three lane states, contention attribution, disabled-but-ready evidence, and
contention deferral.

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

- The native operational window uses full-width **Overview**, **Activity**,
  **Perks**, and **System** pages instead of a permanently narrow control
  sidebar. Overview keeps current control and run-configuration decisions
  together, while service/tunnel operations, host telemetry, and runtime
  evidence live under bounded System subpages. Stable API/SSH/forwarding and
  local-sampling defaults live in a modal **Preferences** surface; saving them
  never starts, stops, or restarts automation or a tunnel. The application
  header groups four separately labelled Linux service, HTTP, API SSH, and ADB
  SSH signals and routes routine navigation through **View**, **Tools**, and
  **Preferences** menus.
- Preferences also contains the default-off BlueStacks recovery master, its
  independently retained lane/deferral options, and exact executable/instance
  settings. Enabling it permits automatic request creation only when Linux
  reports a revision-43 policy lane plus fresh authority; **System >
  Diagnostics** separately offers a confirmed operator
  restart under fresh server-owned Farm authority. An accepted request is
  reconciled independently of later Preference changes. Recovery progress is
  presented as host-maintenance state rather than as an Automation Pause or a
  claim that the game is already running.
- Overview uses one server-authoritative battle-action slot and one contextual
  manual-authority slot: only the matching **Start Battle**/**Attach to
  Battle** and **Take Manual Control**/**Return Control** action is shown. The
  manual Surrender collection choice appears with Take, timed Pause is a
  secondary expansion, and routine explanatory prose collapses unless a
  request, draft, workflow, validation, or error needs attention. Run
  configuration labels current, pending-next/startup, and locally selected
  Strategy separately; the latest completed battle remains useful as a compact
  one-line summary when its detail is collapsed.
- The global status derives run elapsed only from the published current-run
  activity-scope start and the atomic server timestamp. `SCREEN AGE` is the age
  of the latest canonical main-loop observation, not the periodic log summary,
  Windows polling interval, or a promised next screenshot. Wave OCR runs on
  each canonical `RUNNING` frame. A miss, or a temporary non-battle screen in
  the same exact active round, retains the last proven Wave with a muted `*`;
  Wave is never extrapolated. Coins/min remains an independently periodic OCR
  sample and is carried in each later structured snapshot; it receives the
  same `*` while the current screen is off-battle. Tooltips give the source and
  Linux-server-derived age without adding another status column. Extra helper
  captures inside handlers are not canonical observations and do not move
  `SCREEN AGE` or create a misleading next-capture countdown. These values are
  presentation only and never establish battle identity or authority. Server
  revision 49 and `active_battle_screen_metrics_v1` own this screen-metric
  projection; `active_run_metrics_v1` owns the latest accepted save-backed
  checkpoint in the same runtime snapshot. The native status row shows
  whole-run realized CPH, recent CPH from the latest compatible
  save-checkpoint interval,
  whole-run cells/hour, waves/hour, effective speed, and compact checkpoint
  wave and server-derived age. Nonstandard semantic status remains visible. It
  never converts OCR Coins/min into CPH. Polling reads only the
  existing `ActiveRunMetricMonitor` projection; it performs no save acquisition
  or forced write and does not change the independent passive cadence. The
  runtime projection, authority snapshot, and structured observation must all
  carry the same forced-save active-round identity, and every displayed rate
  must belong to the newest single-source checkpoint. A forced target handoff
  that proves the same battle advances this projection without discarding its
  timeline; a non-regressing destination checkpoint can supply the next recent
  interval across the transition, while a cloud rollback starts a fresh recent
  interval and keeps destination whole-run rates. Temporary navigation away
  from the battle screen does not clear either projection while fresh exact-
  owner evidence still proves that same active round. The server clears them
  on owner, freshness, active-round, or identity loss; the native client also
  clears them on a failed status poll. Partial or conflicted checkpoints omit
  rates they do not currently prove. Expected duration, active Peak Coins/min,
  expected-versus-observed requirement detail, recovery countdowns, and
  Return/Extend/Cancel recovery actions remain absent until their owning
  runtime status fields and guarded directives exist.
- Persistent indefinite and timed Pause, explicit Automation Enabled, and
  requested-versus-acknowledged state. The text defines Paused as zero
  automated input while observation continues and does not describe Enabled
  as the game being in `RUNNING`.
- Separate Start/Stop Automation, exact-state Start Battle/Attach to Battle,
  and Take Manual Control/Return Control controls. Their availability and
  pending/acknowledged/rejected/interrupted state comes from Linux, not local
  GUI inference. Start Automation always leaves actions Paused. This contract
  was introduced in server revision 30; the current client requires revision
  48 plus `active_battle_screen_metrics_v1`, `active_run_metrics_v1`,
  `better_control_model_v2`,
  `runtime_control_acknowledgements_v1`,
  `strategy_aware_attach_v1`,
  `bluestacks_maintenance_v2`, `bluestacks_operator_restart_v1`, and
  `bluestacks_listener_lifetime_telemetry_v1`;
  save-backed capture additionally requires `save_backed_setup_capture_v2`.
- A read-only full-width **Perks** page showing the current run's
  monitor-validated saved inventory, level, and last selection wave in
  most-recent-first order. It
  shows the checkpoint wave and local capture time, preserves an unchanged
  scroll position across ordinary five-second status refreshes, and clears on
  an unavailable or changed canonical battle identity. Activity scope may
  regroup the activity view without clearing the Perks projection. The current native client requires
  server revision 32 and capability `current_battle_perks_v1`.
- Take Manual Control selects default minimal or opt-in full collection for a
  later save-confirmed manual Surrender without granting Surrender authority.
  **Capture current setup as…** shows fresh-save captured values, unresolved
  rows, and a fingerprinted Strategy/Base review, then saves only an inactive
  artifact. Captured Strategy drafts remain reopenable in the ordinary native
  authoring catalog together with their own immutable origin, difference, and
  unresolved review—not whichever capture happens to be current. Return
  Control never retains a mismatch-owned capture route; Capture always owns a
  separate explicit refresh.
- **When this battle ends** selects continue automatically, wait, or
  return/stay Home. The compatible `NEXT_BATTLE`, `WAIT`, and `HOME` values
  remain visible only as runtime representation; none is presented as an
  immediate battle command. Continue normally owns direct Retry at the next
  Game Over. If that strategy battle carries repairable configuration
  degradation, Continue instead owns the Home-first repair route described
  above. `WAIT` does not trigger that route, and `HOME` cannot launch the next
  battle. A terminal route that necessarily returns Home can create the exact
  one-shot continuation described above; the selected value alone never does.
  Legacy `RETRY` normalizes to `NEXT_BATTLE`. This contract retains capability
  `terminal_dispositions_v2`.
- Legacy running-battle Strategy Action Gate status remains readable for
  compatibility with server revision 22 and capability
  `strategy_action_gate_v1`. Current runtimes clear legacy session-preflight
  gates and expose their reason as degraded validation evidence; recoverable
  mismatches never create a new gate or Pause. The Automation field and Pause
  coloring continue to show only requested/acknowledged control state.
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
- Explicit catalog-bound **Edit a copy...** for Modules, Target Priority, and
  Orb Distance. Linux materializes and normalizes the exact selected preset;
  meaningful dormant local drafts require replace/retain/cancel, and failures
  preserve both forms. This requires server revision 31 and capability
  `strategy_authoring_preset_local_copy_v1`.
- Authoritative eight-slot Module preset previews plus immutable custom
  **Duplicate preset** and local **Save as preset** workflows. Linux owns the
  merged catalog and validation; native refresh keeps retained selections and
  never bypasses ordinary draft review/publication. This requires server
  revision 25 and capability `managed_custom_module_presets_v1`.
- Persistent numeric game-speed selection from `x0.0` through `x6.0` in
  `x0.5` increments, plus `x6.3` for maximum available. Lower values are exact
  targets across live and future runs. Both clients keep the custom-target
  warning visible and confirm before starting a managed runtime under it; the
  native client also distinguishes saved intent from runtime acknowledgement.
  This requires server revision 14 and capability `game_speed_target`.
- Native Windows host health under **System > Diagnostics** for system CPU,
  memory, processor clock, BlueStacks CPU/RAM/process identity, and local
  publication state. A third row shows residual Other Windows CPU and bounded
  top CPU/working-set attribution after sustained host pressure. Hovering
  the strip shows the attributed PID/name entries, process-scan and total
  sampling costs, BlueStacks I/O, last Linux acknowledgement, and any
  sampler/spool/upload error. The display remains local and current while the
  API is unavailable.
- Advisory startup-check dialogs for requests published by the runtime. The API
  accepts only an option contained in the matching pending request. Retry uses
  fresh evidence; a configured fallback or continuation applies only to the
  named requirement, so unrelated checks remain authoritative. Closing the
  dialog never changes Automation authority or leaves a recoverable error
  blocking. Later success consumes the matching Strategy request so a stale
  warning cannot reopen.
- Non-blocking attached-battle advisories use the same scoped evidence channel
  without becoming a decision gate. They do not open automatically and require
  no response: observation and automation continue degraded. **Review
  preflight advisory** exposes any optional fresh read-only retry or scoped
  continuation already authored by Linux. Persistent Pause remains a separate
  explicit operator action, not an advisory disposition.
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
  `RUNNING`. When a periodic status is due, the runtime reads visible speed
  from the current main-loop frame; an initial miss defers publication through
  two later fresh main-loop frames before reporting the speed unavailable.
  Those retries reuse ordinary captures and send no input. The native and
  browser clients show target and observed values separately, and retained
  Coins/min samples include the corresponding observed speed for mid-run
  analysis. This requires server revision 16 and capability
  `observed_game_speed`.
- Persistent ADB-port selection for the next managed start, plus live handoff
  while the runtime has acknowledged indefinite `PAUSED`. **System >
  Services** shows configured next-start, requested/acknowledged, active
  runtime, and local-draft targets separately. Polling never replaces a dirty
  draft; an invalid or ineligible draft remains visible until explicit
  **Revert** or a successful apply. The existing Linux API remains the only
  apply authority and accepts only an integer TCP port; its validated handoff
  keeps Pause and the former target if new-target connection or screenshot
  validation fails.
- Explicit **Use this PC's emulator** selection from an active ADB reverse
  forward. This supports a different Windows client reusing the same Linux
  port, forces same-port runtime revalidation, and durably attributes completed
  battle CPH to one host or marks the battle partial/mixed. Battle History shows
  each selected host and endpoint; mixed-host and partial runs never enter a
  host-specific CPH cohort.
- Validated strategy selection (`farm_t18`, `farm_t19`, `tournament`, or
  `none`). For an active process, a genuine dropdown change immediately submits
  one ordinary next-boundary request. Programmatic polling/render changes never
  submit. Selecting Current replaces a different pending Strategy, while
  already-current with no pending request and already-pending next-boundary
  selections are no-ops. Acceptance clears dirty state; transport or explicit
  rejection retains the selected value across polling and exposes **Retry next
  battle** without allowing another in-flight request. **Switch this battle** is
  the separate explicit active-adoption request and keeps its fresh-evidence and
  deferred-new-run-gate semantics. When stopped, Start already uses and saves
  the visible selection; **Save startup default** remains the explicit way to
  persist it without starting.
  Successful Strategy publication and restore-as-new select the published ID.
  While the process is active, they automatically submit the same next-boundary
  request, including when the stable ID equals Current; when stopped, they only
  update the visible Start selection. Base publication never submits a process
  request. Publication success is not rolled back if queueing fails. Status
  reports selected, current, and pending Strategies separately.
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
- A live process PID under **System > Services** plus systemd
  MainPID/runtime-lock PID comparison in Diagnostics; stale lock PIDs are never
  promoted as live process identity.
- A compact, normally collapsed most-recent completed-battle summary on
  Overview, with unified completed-run history in a separate native window.
  The history includes
  Farm/Tournament/Milestone classification, strategy, tier, wave, duration,
  Coins/hour, Cells/hour, capture quality, full sections, captured perks,
  resolved settings, game-speed target/timeline, and preflight evidence.
- Local filters for type, Tier, minimum/maximum wave, strategy, and quality.
- Local export of the currently filtered completed-battle summaries as UTF-8
  CSV.
- A draggable layout divider between the history list and selected-battle
  report; the main operational pages no longer depend on persisted sidebar or
  latest-battle splitter sizes.
- Local persistence of the main and Battle History window positions, sizes, and
  maximized states, plus stable string IDs for the selected dashboard and
  System pages and expansion state for optional diagnostic/summary detail.
  Legacy numeric sidebar selections are migrated once; obsolete pane sizes are
  safely ignored. Invalid or off-screen placement is ignored, and minimized
  state is never restored.
- A per-Windows-session instance guard. A repeated launch restores and activates
  the existing operational window rather than creating competing clients.
- A full-width, independently refreshed **Activity** page that defaults to
  concise operational entries in the explicit current-run scope, with
  newest-entry following, non-destructive local clear/restore, and server-side
  diagnostic/all-level filters, without granting general log-file access.
  Hidden-page refresh does not scroll the grid until Activity is selected.
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

1. Extend the existing atomic runtime-owned authority/control snapshot with the
   remaining live-view fields: compact UI detail, active handler, and last
   error. Observation identity/time, battle identity, Wave, retained
   Coins/min, Strategy scope, action gate, startup policy, and exact control
   receipts already use this channel and must not regress to action-log
   authority.
2. Add recovery-timer controls such as extend, cancel, and return-now only after
   those operations have explicit runtime directives and freshness/authority
   checks. The GUI must not implement them as direct taps.
3. Extend the implemented active-battle → Home Resume Battle safety yield to
   broader likely manual-player activity, then show configurable grace-period
   countdown and ownership in the GUI.
4. Add battle comparisons, trend charts, and aggregate rates by strategy, tier,
   profile, battle type, and date range.
5. Add opt-in notifications for battle completion, invalid capture quality,
   stale runtime, control acknowledgement timeout, and degraded preflight.

Repository implementation of save-backed Attach/Return reconciliation and
**Capture current setup as…** is included in revision 29. Revision 30 adds the
typed capture authority outcome, inspect-without-retry terminal presentation,
and separate explicit retry action under `save_backed_setup_capture_v2`. The
Better Control Model backlog retains the unperformed Windows usability and
natural-boundary live validation; those checks are not implied by the
repository checkpoint.
