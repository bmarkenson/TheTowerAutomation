# Managed Runtime Operations

Complete [`live_preflight.md`](../live_preflight.md) before changing a live
service, target, Strategy, or attachment. Linux unit installation and upgrade
belong to [`deploy/systemd/README.md`](../../deploy/systemd/README.md); native
client operation belongs to the
[`Windows README`](../../windows/TheTower.ControlSurface/README.md).

## Start or stop automation

**Start Automation** and **Stop Automation** change only the fixed managed
process lifecycle. Start launches the service with Automation Paused and no
battle workflow selected. Wait for fresh live observation, then choose a
separate available **Start Battle** or **Attach to Battle** action. Do not
interpret a live process as enabled input or a stopped process as a terminal
policy.

**Enable Automation** changes action authority only. At an idle managed Home
boundary—before the first battle or after any later one—Enable does not
serialize a Home baseline, run configuration setup, recover navigation, claim
a Tournament validation battle, or dispatch New Battle/Resume Battle. Those
paths require an exact immediate owner: the separate matching battle intent,
an already-bound terminal continuation, or the explicit one-shot validation
workflow. An explicitly requested setup capture remains a separate workflow
under its own exact evidence and serialization authority.

**Start Battle** is available only from fresh, owner-matched Home **New
Battle** evidence. If requested while Paused it remains `awaiting_enable`;
explicit **Enable Automation** revalidates the same runtime, target, workflow
operation, and boundary. It then forces serialization and requires
`round_active=false` before normal new-run gates receive action authority.
When the selected Strategy declares a numeric tier, the runtime reads the Home
tier selector, moves it one verified step at a time in either direction, and
rechecks the requested tier on a fresh frame immediately before Battle. It
does not tap Battle when the current tier, an individual selector transition,
or the final tier cannot be proved. A strategy without a numeric tier retains
the existing launch behavior. Once the verified Home control is tapped, status
reports `action_dispatched`; the workflow continues to suppress unrelated
input until the first stable `RUNNING` frame forces and binds the new
`ActiveRoundIdentity`, or a visible interrupted/failed result occurs.
If Pause, Stop, or Take Manual Control arrives during a Home configuration
route, setup yields at the first denied input and performs no cleanup action.
Observation and acknowledgement continue. A later Enable restores Home only
when the original workflow still owns the exact runtime, target, operation,
and visible boundary; otherwise that pending recovery is discarded. Activity-
log scope creation or rotation never rejects Start.

Stopping interrupts unfinished battle and manual-control workflows. If fresh
status proves an exact active battle already owned by automation, complete Stop
also records a one-shot handoff for that battle, regardless of whether
automation originally started it or attached later. Start consumes that
handoff with a fresh ordinary Attach workflow and forced save; wave progression
does not matter because the proof is the battle identity. The service restores
Enabled authority only after the same identity is adopted. A changed or ended
battle, target mismatch, or unavailable proof leaves the replacement Paused
for explicit intent. Without a handoff, Start retains its normal Paused,
explicit-intent behavior. Repeating an already satisfied Start or Stop is
reported as a no-op; the old one-step attached reload remains retired.

## Set what happens when a battle ends

**When this battle ends** is future policy, not a Home command. **Continue
automatically** normally taps Retry directly when the next ordinary Game Over
is handled. If that strategy battle carries a flagged configuration problem at
Game Over, Continue instead returns Home, applies any pending next Strategy,
and runs that profile's normal bounded setup before starting another battle. A
failed Go Home action retries from fresh terminal evidence; exhausted setup is
reported and the next battle still starts degraded. **Wait** retains the
supported terminal boundary, and **Return to / stay Home** follows its verified
Home route without authorizing a launch. Selecting Continue while already at
Home records and acknowledges only that future policy; it does not run save
preflight, repair configuration, tap Battle, or tap Resume.

Some completed-terminal workflows must reach Home before another battle can
start: degraded-battle repair, No Strategy post-run inventory, and Tournament
Results dismissal. If Continue was already selected
for that exact terminal boundary, the runtime may carry one process-local
continuation through the owned Home work. It is bound to the exact runtime,
target generation, terminal active-round identity, operation, and terminal-time state/policy request IDs;
it accepts only fresh **New Battle**, runs normal new-run gates, and is consumed
only after one verified dispatch. Pause/Enable or policy request changes,
manual/workflow supersession, Resume Battle, changed binding, process restart,
or unexpected manual activity cancels it. Changing Wait/Home to Continue after
the terminal or at Home never manufactures that permission; use **Start
Battle** if an immediate start is wanted.

Home save/setup can take long enough for operator intent to change. Immediately
after acquisition/setup, again before Home handling, and finally after fresh
control verification but before the tap, the runtime must still find the same
Start/Attach/Return request ID, intent, status, control type, and lifecycle
authority. A replacement request starts from its own next observation and
gates; it never inherits the earlier request's launch. Pause blocks the tap and
suspends unconsumed save carry so a later Enable uses fresh save or UI evidence;
Stop discards the process-local carry. Manual control, a replacement request,
or another workflow also discards the old transition binding. `WAIT` is only
the future terminal disposition: it neither blocks an explicitly authorized
Start nor invalidates the resulting first-`RUNNING` evidence. The terminal
continuation performs the same ownership check and clears its published pending
state when authority or manual ownership supersedes it.

## Attach to a current battle

**Attach to Battle** is available only from fresh active-battle or Home
**Resume Battle** evidence. It never starts a new battle as a fallback and does
not adopt the observed battle merely because the intent was accepted. The
runtime first revalidates the exact PID, ADB target/generation, workflow
operation, and observed boundary.

The runtime remains input-blocked in `validating_save` while it performs the
guarded exact-target serialization required by the observed boundary and
restores the source. Active-battle Attach needs one proof. Home Resume needs
two: one forced save identifies the Resume target before the tap; a definite
tap rearms identity, and the first stable Running frame forces again before
adoption. A usable save must provide `ActiveRoundIdentity`. With no prior
identity it is adopted; an
equal ID proves the same battle; a different ID proves a later battle and
discards old battle-local state. Battle History, timestamps, visual similarity,
and activity scope never substitute. A safely restored transient failure gets
bounded forced retries and a retryable workflow result rather than releasing
battle input. The Attach request freezes the complete accepted
Strategy definition at the request boundary, so a later selection cannot
change the battle being attached:

- **No Strategy** intentionally adopts the battle as an observer and is not by
  itself degraded.
- A selected Strategy whose battle kind and, for Farm, tier are proved
  compatible becomes the active Strategy for that battle. A configuration
  mismatch is recorded as degraded, but Attach does not repair the running
  battle.
- An incompatible or unprovable selected Strategy adopts the battle as a
  degraded observer. The selection remains pending for the next safe battle
  boundary.

That degraded observer cannot later be turned into a Strategy-run battle with
**Switch this battle**. The client disables the action when it has authoritative
status; Linux independently downshifts any raced or older active-battle request
to the next boundary without changing its request identity. Intentional **No
Strategy** observation remains eligible for a later explicit switch.

All attached-battle configuration checks are observational. They may inspect
supported UI, but cannot change Damage Slider, Orb Distance, Auto Pick, Poison
Swamp Stun, a preset, a loadout, or any other configuration. A check failure,
unsupported configuration field, or status-reporting failure completes
degraded after identity succeeds. Only loss of the exact owner, target,
canonical identity, action authority, source restoration, or certainty about
a dispatched input is catastrophic and leaves Automation Paused. A failed
receipt write never turns a successfully adopted battle into a global input
hold; the runtime retains its exact process-local claim and retries reporting.

## Take and return manual control

**Take Manual Control** first requests and waits for an acknowledged indefinite
Pause. Observation may continue, but automated device input is zero. Choose how
a later manual Surrender should be handled at that boundary:

- **Exclude manual Surrender stats** (default) detects the terminal from the
  bound natural save, writes a minimal nonrepresentative/excluded record, and
  skips terminal collection UI.
- **Collect manual Surrender stats** opts into the ordinary terminal collection
  path after save-backed cause confirmation.

Neither choice tells automation to Surrender, and there is no manual Surrender
command to announce in advance.

**Return Control** records fresh visual observation while Pause remains in
force; it does not read or serialize the save. It is available only at Home
New, Home Resume, active battle, or bound Game Over evidence. Explicit
**Enable Automation** then authorizes only reconciliation. At Home New the
runtime forces an inactive proof and closes the retained ID. At active battle
it forces `ActiveRoundIdentity`. At Home Resume it forces once before the tap,
then rearms and forces again on the first stable Running frame before adoption.
In either active result, equal resumes the same battle; different discards old
battle-local state and adopts the manually started
successor before configuration checks run. Game Over consumes its lifecycle-
issued natural bundle. No path waits for a save, polls History, or treats log
scope as identity. Once identity succeeds, an unavailable configuration/report
projection may use its supported UI route and complete degraded. Do not use
Enable as a shortcut around Return.
If a Home New refresh loses its source restoration, owner, target, identity, or
authority binding after backgrounding, Return becomes failed/interrupted and
Automation remains Paused.
Do not retry by repeatedly selecting Enable; start a new explicit Return only
after reviewing the reported boundary.
At Home New, a mismatch is repaired immediately because the boundary is already
safe. If evidence is unavailable or bounded repair exhausts, Return completes
degraded with the failed check and exact reason. Automation remains Enabled;
the workflow does not wait for a manual correction or another Enable.

Expected manual-boundary behavior is explicit:

- Pause or Stop makes the retained battle ID comparison-only; neither action
  assumes the battle stayed active.
- If the operator Surrenders and returns at Home New, Enable/Return forces an
  inactive save and closes the old ID. A later Start independently forces Home
  inactive again, dispatches Battle, and force-binds the successor at
  `RUNNING`.
- If the operator Surrenders, manually starts another battle, and then Enables
  or restarts/Attaches at `RUNNING`, forced serialization returns a different
  ID. The runtime discards the old battle-local state and adopts the successor.
- If the battle never changed, the same forced ID permits same-battle receipt
  reuse. A restarted managed process begins Paused. A valid complete-Stop
  handoff creates and completes its fresh Attach automatically; otherwise use
  explicit Attach (or Start from Home New).

## Capture a manually changed setup

At verified Home New, Home Resume, or active battle, with Automation Enabled
and no competing workflow, choose **Capture current setup as…**. The runtime
briefly performs its guarded serialization and presents representable values,
unresolved fields, and an optional comparison Base. Automation Paused cannot
perform this lifecycle refresh; the UI reports that outcome and never consumes
a cached save.

An exact or forward-compatible save revision may supply only the checks named
by its resolved mapping's explicit compatibility allowlist; every other field
stays unresolved. An unsupported or structurally incompatible revision, a
missing runtime projection, or incomplete round identity reports Capture as
`unavailable` and opens no configuration UI. When the guarded refresh has
proved source restoration, that outcome preserves the prior action-authority
state and does not disable ordinary Battle History/configuration UI monitoring.
Capture is the deliberate exception to full UI fallback because no supported
UI route can produce one coherent, reviewable authoring snapshot. Fresh
active/resumable evidence that contradicts the requested battle
reports `failed`, releases capture ownership, and preserves the prior authority
when source restoration is proved. A proved Home New contradiction, or an attempted
lifecycle transition whose source restoration cannot be proved, reports
`failed` and persists Automation Paused. Review the reported boundary before
requesting another capture.

Save either a new immutable managed Module preset or a custom Strategy draft.
The Base is comparison-only, unresolved fields remain explicit, and saving
does not publish, select, queue, activate, or apply anything. Captured Strategy
drafts remain in the Strategy authoring catalog and can be reopened later for
the ordinary Linux Validate → Review → Publish flow. Reopening shows that
draft's stored origin, captured-versus-Base difference, and unresolved rows.
The CLI requires `capture-setup review-strategy ...` followed by a separate
`capture-setup save-strategy ... --review-fingerprint <sha256>`; it never
accepts its own just-printed review automatically. A collision requires a new
ID unless the Strategy draft embeds the exact evidence proving recovery of a
previous atomic-create receipt failure.

If the runtime completed serialization but could not write the ready or
terminal receipt, it retries only that atomic receipt from its exact
process-local result. It does not background the game again or change action
authority. Reopening `saved`, `cancelled`, `unavailable`, `interrupted`, or
`failed` shows that terminal result without issuing input. Use the separate
**Try capture again** action to request a new serialization. An orphaned
`capturing` ledger after process loss is not replay authority.

## Switch the live ADB target

1. Select indefinite Pause and wait for acknowledgement; timed Pause is not
   accepted.
2. Move the emulator, enter the new exact localhost port, and select Switch.
3. Require the new target lock, `device` transport, supported fresh frame, and
   runtime handoff acknowledgement before the old lock is released.
4. Verify the fresh screen and select **Automation Enabled** only when
   appropriate.

A failed handoff remains Paused and retains the old runtime target while the
control service may continue bounded registration retries for the saved next-
start target.

### Move the emulator between Windows PCs

Use this procedure when BlueStacks and The Tower move from one Windows PC to
another while the Linux control service and managed automation process remain
the same. It covers both distinct Linux ports and intentional reuse of the
former PC's Linux port. The same procedure applies when moving back: exchange
the source and destination PC names and perform every step again.

Opening a Control Surface on another PC does **not** select that PC's emulator.
The client cannot safely infer that a changed Windows process is the intended
target. Saving Preferences also does **not** change a desired/running SSH
forward or the live Linux runtime target. The explicit
**Use this PC's emulator** action is the handoff boundary.

#### Understand the four independent layers

| Layer | Example | What it proves |
| --- | --- | --- |
| Windows BlueStacks listener | Windows `127.0.0.1:5555` | BlueStacks is accepting local ADB connections on that PC. It does not prove an SSH forward or usable game frame. |
| ADB reverse forward | Linux `127.0.0.1:5565` → Windows `127.0.0.1:5555` | This PC's tunnel host owns the named Linux listener and forwards it to this PC. It does not select the Linux runtime target. |
| Managed runtime target | Linux `localhost:5565` | The running automation process is registered to that Linux endpoint. It does not identify which Windows PC currently owns a reused port. |
| Explicit emulator host | `MAIN-PC`, stable host ID, ports, and optional exact BlueStacks process | Linux has validated and acknowledged this Windows client as the selected emulator host for attribution and current target authority. |

The header's **Linux service**, **HTTP**, **API SSH**, and **ADB SSH** indicators
are also independent. An active API tunnel does not prove an ADB tunnel, and
an active ADB tunnel does not prove HTTP compatibility or a valid emulator
screen.

The per-user `TheTower.TunnelHost.exe` owns the API and ADB SSH children.
Closing the Control Surface disconnects the GUI but deliberately leaves every
desired tunnel running. Closing BlueStacks also does not release the Linux
reverse-listener port. An explicit **Stop ADB forward** clears that desire and
releases the listener. When no GUI is connected and neither tunnel is desired,
the tunnel host exits normally after about 15 seconds.

#### Choose the Linux-port policy

The Windows BlueStacks listener and the Linux reverse-listener port are
separate values. BlueStacks may listen on Windows port `5555` on every PC while
the Linux ports differ.

| Policy | Example | Tradeoff |
| --- | --- | --- |
| Stable Linux port per Windows PC | Main PC: Linux `5555` → Windows `5555`; laptop: Linux `5565` → Windows `5555` | Lowest-conflict workflow. The ports do not contend, although the golden path still stops the unused source forward. **Use this PC's emulator** remains required on every move, and the Linux runtime target text changes. |
| Reuse one Linux port | Either PC: Linux `5555` → that PC's Windows `5555` | Keeps one familiar Linux target, but the source PC must release the port before the destination can bind it. The target text may not change, so explicit host selection is the only safe ownership boundary. |

Do not use the port number as a substitute for Windows-host identity. A
successful same-port selection creates a new request, validates a fresh frame,
and advances the runtime target generation even though `localhost:<port>` is
textually unchanged.

For the cleanest performance attribution, move at a natural completed-battle or
Home boundary before the next battle begins. Select **Wait** or
**Return to / stay Home** as the battle-end policy in advance when appropriate;
never Surrender an operator-owned battle merely to manufacture a handoff
boundary. A mid-battle move is supported when it is operationally necessary,
but that completed battle will intentionally be classified as mixed-host and
excluded from host-specific CPH baselines.

#### One-time preparation on each Windows PC

Before relying on a PC as a handoff destination:

1. Deploy the complete matching publish directory. Confirm
   `TheTower.ControlSurface.exe` and `TheTower.TunnelHost.exe` are adjacent;
   copying only the GUI executable is incomplete.
2. Confirm passwordless SSH and host-key trust for the configured Linux
   destination. The tunnel host is intentionally noninteractive.
3. In **Preferences**, configure:
   - the Linux SSH destination;
   - the normal API ports, usually `8787` on both sides;
   - the actual Windows BlueStacks ADB listener port, usually `5555`; and
   - this PC's chosen Linux ADB-forward port.
4. Start BlueStacks and confirm **System > Connections** reports a Windows ADB
   listener on the configured Windows port.
5. Confirm the client can establish the API tunnel and reach a compatible
   Linux API before depending on it for Pause or handoff control.

Preferences are defaults for the next explicit Start or Restart. If a tunnel
host already has a desired or failed endpoint, changing Preferences does not
silently replace that endpoint.

#### Phase 1 — Pause and release the source PC

Perform these steps while the source PC's API connection and current emulator
path still work:

1. Open the source PC's Control Surface.
2. Select indefinite **Automation Paused**. Do not use a timed pause.
3. Wait until the runtime—not merely the button—reports the current Pause
   request as **acknowledged**. The action-authority display must say
   Automation Paused.
4. Go to **System > Connections** on the source PC and select
   **Stop ADB forward**. Wait for the ADB reverse-forward panel to say
   **Stopped**. The API tunnel may remain active.
5. Only after Pause acknowledgement and forward release, close The Tower or
   BlueStacks on the source PC and close its Control Surface if desired.

This order is deliberate: Pause acknowledgement is easiest to prove while the
old emulator path still works, and the destination cannot reuse the same Linux
port until the old reverse forward has actually stopped.

Closing only BlueStacks or the Control Surface is insufficient. If the source
GUI was already closed, reopen it; it should attach to its still-running
per-user tunnel host and recover the ADB forward's desired and active state.
Stop the ADB forward explicitly. Do not terminate a generic `ssh.exe` or guess
which process owns the port.

If the source PC is unavailable and its forward may still own the desired
Linux port, use a different free Linux port on the destination. Do not displace
an unidentified listener merely to preserve a preferred port number.

#### Phase 2 — Establish the destination PC's paths

1. Start BlueStacks on the destination PC, launch The Tower, and wait for the
   intended current game screen to be fully visible.
2. Start the destination PC's Control Surface and open
   **System > Connections**.
3. Confirm the tunnel host is available. Establish the API tunnel if needed,
   then require:
   - **API SSH: Active**;
   - **HTTP: Connected**; and
   - a compatible, reachable Linux service.
4. Review **Configured connection defaults**. Verify the displayed mapping is
   the intended `Linux localhost:<L> → Windows localhost:<W>` pair.
5. Compare those defaults with the ADB reverse-forward panel's desired and
   active endpoint. If the panel retains an old, wrong, retrying, or conflicted
   endpoint, select **Stop ADB forward** first. Wait for **Stopped**; the button
   will then return to **Start ADB forward** and the next Start will use the
   saved defaults.
6. Select **Start ADB forward**. Wait for the panel and header to report the
   ADB tunnel **Active**, with the exact intended Linux and Windows ports.

The green Windows-listener line alone is not enough. The SSH reverse forward
itself must be Active. If the button says **Retry ADB forward**, a desired
forward already exists in a conflict or fault state. Retry is appropriate only
when its displayed endpoint is already correct and the cause has been fixed;
otherwise Stop it, correct Preferences, and Start it again.

For same-port reuse, a remote bind conflict normally means the source PC's
forward—or another reverse listener—still owns that Linux port. Stop the exact
source forward or deliberately choose another Linux port. The destination
client never kills or adopts another client's tunnel.

#### Phase 3 — Explicitly select and validate the destination host

1. Reconfirm that indefinite Pause is still acknowledged. If automation is
   running and the acknowledgement is absent, request Pause again and wait.
2. In the destination PC's **System > Connections** ADB reverse-forward panel,
   select **Use this PC's emulator**.
3. Wait for all of the following:
   - the selection status names the destination Windows PC;
   - it shows the active `Linux localhost:<L> → Windows localhost:<W>` mapping;
   - it says **acknowledged**, not `awaiting runtime validation`;
   - the requested and active Linux ADB target are the intended endpoint;
   - Current Status has a fresh heartbeat and a fresh, correct game screen; and
   - no emulator-location or target-handoff error is displayed.

For same-port reuse, the requested and active target text may look identical
before and after the click. The newly named Windows host and its
**acknowledged** status are the visible proof that Linux performed the
same-port revalidation. Reopening the GUI, starting the forward, or seeing
`device` transport does not replace this step.

A failed selection keeps automation Paused and retains the former runtime
target/generation. Correct the reported tunnel, listener, compatibility, or
frame problem and submit a new explicit selection. Do not Enable automation to
test whether the failure matters.

#### Phase 4 — Verify and resume deliberately

Before selecting **Automation Enabled**, verify this checklist from the
destination client:

- the selected emulator host is the destination PC and is acknowledged;
- ADB SSH is Active on the intended endpoint;
- HTTP is Connected and the Linux service is reachable;
- the managed runtime target matches that endpoint;
- Current Status and heartbeat are fresh;
- the visible screen is the expected battle, Home, or supported boundary; and
- no target, host-selection, save, or action-authority warning remains.

Then select **Automation Enabled** only if normal automated input should resume,
and wait for the runtime to acknowledge that request. A successful live target
handoff keeps the same managed process and its in-memory Strategy/session state;
it does not require Stop/Start or Attach merely because the Windows PC changed.
If the managed process was separately stopped or replaced, follow the normal
Start/Attach procedure instead of assuming this in-place rule applies.

#### Host attribution and CPH consequences

Every explicit selection records the stable Windows host ID/name, Linux target,
Windows listener, target generation, and exact BlueStacks process lifetime when
available. Completed Battle History preserves that timeline.

- A battle explicitly attributed to one host from its beginning is eligible
  for that host's CPH and performance baselines.
- A battle that changes Windows hosts is marked `mixed_hosts`. Both transitions
  remain visible, but the battle is excluded from host-specific CPH comparison
  and automatic severe-loss calibration.
- A battle that began before explicit host attribution is marked `partial` and
  is likewise excluded from host-specific baselines.

This exclusion is intentional: measurements from two PCs or an unknown opening
segment must not train one PC's performance model.

#### Why **Use this PC's emulator** may be disabled

Hover the disabled button for its current blocker. The button requires all of
these conditions:

| Requirement | Recovery |
| --- | --- |
| This client's ADB reverse forward has an **Active** endpoint | Start or repair the ADB forward; Windows-listener detection alone is insufficient. |
| A live automation process is indefinitely Paused and that exact request is acknowledged | Select indefinite Pause and wait for runtime acknowledgement. A timed Pause is ineligible. |
| The managed ADB lifecycle is available | Restore the compatible Linux API/service and refresh status. |
| The server supports emulator-host selection | Update/restart the Linux API and use the matching complete Windows package. |
| No selection request is already in flight | Wait for the current request to finish and review its result. |

The click also checks that BlueStacks is listening on the active forward's
Windows destination port. A stopped or differently configured listener can
therefore produce a clear error after an otherwise enabled click.

#### Tunnel-host and port recovery

Use the narrow recovery matching the symptom:

| Symptom | Meaning and recovery |
| --- | --- |
| Preferences show the new port, but the ADB panel shows the old endpoint | Preferences changed only the default. Select **Stop ADB forward**, wait for Stopped, then **Start ADB forward** to adopt the default. |
| There is no Start button; the button says Retry | The tunnel is still desired but faulted/conflicted. If its endpoint is wrong, Stop it before starting with corrected defaults. If it is correct, fix the reported cause and Retry. |
| `remote port forwarding failed for listen port <L>` or **Conflict** | Another listener owns Linux port `<L>`, commonly the former PC's still-desired forward. Stop that exact forward or select a different free Linux port. |
| ADB SSH is Active, but Linux still names the former PC | Transport recovered, but host selection did not occur. While Paused and acknowledged, click **Use this PC's emulator** on the destination PC. |
| ADB transport says `device`, but Current Status is stale or wrong | Transport is not frame validation. Keep Pause, bring The Tower to the intended supported screen, and submit the host selection again. |
| API SSH is Active but HTTP fails | The SSH process and HTTP/API service are separate. Restore the API service/compatibility without changing the ADB forward. |
| Restarting the Control Surface makes the tunnel host appear | The new GUI successfully launched or attached to the companion. Recheck both tunnel desires/endpoints; a new host never replays desired tunnels automatically. |
| The tunnel host appears and then exits with no GUI and no desired tunnels | This is the normal approximately 15-second idle shutdown. Open the GUI first or start a desired tunnel. |
| **Restart tunnel host** is used | It explicitly stops that companion and its owned API/ADB SSH children. The replacement starts with both desires off; manually Start API and ADB again. It is companion recovery, not the normal way to change a port. |

If the tunnel host is unavailable, first restart the Control Surface once and
allow its normal startup/attach attempt to finish. Verify the complete package
contains both adjacent executables. For diagnostic manual launch, leave the GUI
open so its five-second status poll can attach before the 15-second idle exit:

```powershell
$p = Start-Process .\TheTower.TunnelHost.exe -PassThru
"Started PID $($p.Id)"
$p.WaitForExit()
$p.Refresh()
"Exit code: $($p.ExitCode)"
```

Exit code `0` after approximately 15 idle seconds means no GUI or desired
tunnel kept the host alive. An immediate exit code `0` can instead mean another
per-user tunnel host already owns the singleton. Exit code `1` is a host
failure; inspect
`%LOCALAPPDATA%\TheTower\tunnel-host-startup.log`. Invoking this Windows-
subsystem executable with `& .\TheTower.TunnelHost.exe` can return PowerShell
immediately and leave `$LASTEXITCODE` blank, so that form does not capture the
eventual exit.

#### Compact handoff checklist

Use this only after understanding the detailed procedure above:

1. Source: request indefinite Pause and wait for **acknowledged**.
2. Source: **System > Connections > Stop ADB forward**; wait for Stopped.
3. Source: close BlueStacks/GUI only after the forward is released.
4. Destination: start BlueStacks and The Tower; confirm the Windows listener.
5. Destination: establish API SSH, HTTP, and compatible Linux service.
6. Destination: verify Preferences and the exact ADB default mapping.
7. Destination: Stop any stale desired endpoint, then Start the intended ADB
   forward; wait for Active.
8. Destination: reconfirm Pause acknowledgement.
9. Destination: click **Use this PC's emulator**.
10. Wait for the destination host, exact mapping, and **acknowledged** status.
11. Require a fresh correct screen and heartbeat with no handoff error.
12. Enable automation deliberately and wait for acknowledgement.

Never skip steps 1, 2, 9, or 11 merely because the same Linux port worked on
that PC before.

## Change Strategy at a boundary

The GUI reports accepted request, current Strategy, pending Strategy, and live
acknowledgement separately. During a battle, a selection queues for the next
authoritative boundary; a later selection replaces it, and selecting Current
cancels another pending value. The current Strategy owns the completed record
and Game Over hook. At verified Home **New Battle** or Workshop the request may
apply immediately, including while Paused; Home **Resume Battle** is not a
boundary. The managed next-start value is updated too, but only runtime
acknowledgement proves live application.

Tournament validation, launch, observer, and attachment rules are in
[`architecture/runtime.md`](../architecture/runtime.md#tournament-exclusive-validation-and-observer-profile)
and the [native-client procedure](../../windows/TheTower.ControlSurface/README.md#connect).
