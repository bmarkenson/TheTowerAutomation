# Native Windows Control Surface

This WPF application is the primary desktop client for the Linux control
surface API. It has no browser dependency. The published package contains the
self-contained single-file GUI and a separate self-contained, headless
per-user tunnel host.

## Publish

Install the .NET 8 SDK on the Windows build machine, then run PowerShell from
this directory:

```powershell
.\publish.ps1
```

The complete package is written to:

```text
publish\win-x64\TheTower.ControlSurface.exe
publish\win-x64\TheTower.TunnelHost.exe
```

The target PC does not need the .NET runtime because the publish is
self-contained. Deploy the complete `win-x64` directory; copying only the GUI
executable deliberately fails closed because it would leave no authoritative
SSH owner. The output directory is ignored by Git.

The same Windows executable can be compiled on Ubuntu 24.04 even though WPF
cannot be run there. Do not use Ubuntu's `dotnet-sdk-8.0` package for this
build: Canonical's SDK omits `Microsoft.NET.Sdk.WindowsDesktop`, so it fails
with `MSB4019` before Windows targeting-pack restore.

Install Microsoft's official SDK side-by-side without removing the Ubuntu
package:

```bash
THETOWER_DOTNET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/thetower-dotnet"
curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/thetower-dotnet-install.sh
bash /tmp/thetower-dotnet-install.sh \
  --version 8.0.423 \
  --install-dir "$THETOWER_DOTNET_DIR" \
  --no-path
```

Then publish from the repository root:

```bash
windows/TheTower.ControlSurface/publish-linux.sh
```

Set `THETOWER_DOTNET=/absolute/path/to/dotnet` to select a different Microsoft
SDK installation. The script rejects SDKs missing WindowsDesktop before it
starts the build. It publishes both projects into a staging directory, verifies
both required executables, and replaces the prior package only after both
succeed. The GUI project explicitly enables Windows targeting; copy the
complete result directory to Windows for runtime testing.

## Connect

The GUI starts `TheTower.TunnelHost.exe` on demand and controls it through a
length-prefixed JSON v1 named-pipe protocol. The stable pipe and single-instance
mutex names are derived from the current Windows user's SID, and the server
uses `PipeOptions.CurrentUserOnly`. The GUI never starts, adopts, or kills an
`ssh.exe` process directly.

The host owns two independently controlled Windows OpenSSH processes. The API
tunnel preserves the Windows-local forward from `127.0.0.1:8787` to the Linux
API at `127.0.0.1:8787`. The ADB reverse forward exposes the PC's Windows-local
BlueStacks listener through a configurable Linux loopback port:

```text
-L 8787:127.0.0.1:8787
-R 127.0.0.1:<linux-adb-port>:127.0.0.1:<windows-bluestacks-port>
```

Enter the Linux destination as an SSH config alias, host, or `user@host`.
Leave the API ports at 8787 unless the Linux API is configured differently.
The Windows BlueStacks and Linux ADB ports are separate saved settings that
both default to 5555. Keep them equal for one PC, or assign each PC a distinct
Linux port such as 5555 and 5556 while retaining its actual Windows listener
port. The Linux endpoint is always requested on `127.0.0.1`; the GUI does not
offer a non-loopback ADB bind.

Each process uses BatchMode, strict host-key checking, a bounded connect
timeout, keepalives, and `ExitOnForwardFailure`. An ADB remote-listener conflict
therefore does not stop the API tunnel. The Setup tab reports whether Windows
has a TCP listener for the configured BlueStacks port separately from whether
OpenSSH accepted the Linux reverse listener. Raw SSH exit detail is retained.
A bind or SSH-policy conflict keeps that tunnel desired but pauses its retry
until the operator changes the port or policy and selects Retry/Restart. Other
unexpected exits retry independently after 5, 10, 20, then at most 30 seconds.
An explicit Stop clears only that tunnel's desired state and cancels its retry.

The host joins itself to a Windows Job Object configured with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before it starts any SSH process. Its
forwarding and fixed-service-query children inherit that job, so a host crash,
forced exit, ordinary shutdown, or user logoff cannot leave unmanaged SSH
orphans. It never enumerates or adopts a pre-existing `ssh.exe`.

Passwordless public-key authentication must already work, and the host key
must already be trusted. The one-time interactive setup or manual equivalent is:

```powershell
ssh <linux-user>@<linux-host>
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
ssh -N -R 127.0.0.1:5555:127.0.0.1:5555 <linux-user>@<linux-host>
```

Starting the API tunnel automatically selects
`http://127.0.0.1:<local-port>` and connects. The standalone **Connect** button
supports an already-running manual tunnel or an authenticated TLS reverse
proxy. The application persists the URL, SSH destination, and port preferences,
but the bearer token is held only in memory and is never saved. The companion
persists only validated tunnel configuration in
`%LOCALAPPDATA%\TheTower\tunnel-host.json`; desired/running state is never
written there.

Closing the GUI disconnects its pipe but does not stop a desired tunnel.
Reopening the GUI attaches to the existing per-user host and immediately
recovers each tunnel's desired/observed state, SSH PID, active endpoint,
retry/conflict state, and last raw diagnostic. A newly created host loads saved
configuration with both tunnel desires off; the operator must explicitly start
each one. When neither tunnel is desired and no GUI remains connected, the host
exits after 15 seconds. It is not registered for login startup and has no tray
UI. Normal Windows logoff terminates the interactive user's host and its Job
Object children.

The companion also owns bounded SSH status queries and Start/Stop/Restart for
the fixed `thetower-control-surface.service` user unit. IPC requests carry only
the action enum and validated connection configuration; there is no remote
command, unit, path, or shell-input field. HTTP API traffic remains in the GUI.

The Setup tab shows the connected host version, instance, and PID. A protocol
mismatch disables tunnel/service commands and requires explicit **Restart
tunnel host...** confirmation. A compatible restart asks the existing host to
stop its owned children; an incompatible replacement verifies the reported PID,
start time, and executable path before terminating it. In either case the new
host loads configuration but does not replay tunnel desire. Headless startup
failures are retained in
`%LOCALAPPDATA%\TheTower\tunnel-host-startup.log`.

The main and Battle History windows also remember their normal position, size,
and maximized state in `%LOCALAPPDATA%\TheTower\control-surface.json`. The main
window additionally remembers its control-pane width, latest-battle height,
selected control tab, and the expanded state of Previous Game Screen, Host
Health, and the latest-battle summary. **Reset layout** restores those pane
defaults. A saved position that no longer leaves a usable title bar on the
current virtual desktop is ignored, and a window is never reopened minimized.

Only one control-surface process is allowed per Windows session. Launching the
application again restores and foregrounds the existing main window, or flashes
it on the taskbar if Windows declines the foreground request.

The operational window keeps the most recent completed battle visible without
devoting the normal control workspace to the full history. Select **Open battle
history...** to open the separate completed-battles window. That window merges
Battle and Tournament records and classifies Farm, Tournament, and Milestone
using strategy plus terminal-screen evidence. It filters by type, Tier, wave
range, strategy, and capture quality. The report banner includes Coins/hour and
Cells/hour, followed by a collapsible per-section tree containing complete
Stats rows, Game Stats-only and derived values, Coins/min progression, and
approximate Second Wind/Demon Mode/Nuke activation waves. Second Wind rows also
show the approximate 400-wave re-arm estimate recorded from each observed
activation. Expanded sections use high-contrast, table-style Stat/Value rows.
Separate tabs retain the captured perk order, resolved run settings, and
observed runtime/preflight evidence.
The battle list can export the currently filtered rows as an Excel-compatible
UTF-8 CSV without requesting any additional Linux-side authority.
**Discard selected...** confirms the exact record identity, then moves its JSON
and Markdown files into Linux quarantine. The default 30-day recovery window
and permanent purge are enforced by server revision 8; the dialog reports the
recorded deadline after a successful discard.
The Strategy filter is an exact-match dropdown populated from the currently
loaded records. Periodic battle refreshes leave an unchanged list alone and
defer genuine updates while a Type, Strategy, or Quality filter menu is open so
the popup and selected battle remain stable.

The left workspace uses full-height **Controls**, **Process**, **Setup**, and
**Details** tabs instead of dividing its height among several independently
scrolling cards. Everyday Automation Paused/Enabled, explicit battle workflow,
manual-control, game-speed, future terminal-policy, strategy, and run-
configuration actions remain on Controls. Service state, PID, ADB target,
Start Automation, and Stop Automation are on Process. API and SSH fields are confined to the
Setup tab, which scrolls when its independent API and ADB tunnel controls do
not fit; the optional bearer token remains memory-only. Detailed lock and
runtime evidence is on Details.

The top bar keeps four different health signals visible: the fixed Linux API
service's systemd state, HTTP reachability, the Windows-local API SSH tunnel,
and the ADB reverse-forward SSH tunnel. **Start API**/**Stop API** and
**Restart API service** affect only
`thetower-control-surface.service`. **Restart SSH...** offers separate API- and
ADB-tunnel actions so an ADB bind conflict cannot disturb API control. The
Process tab's automation-service state remains separately labelled
**Automation service**.

Drag the main vertical divider and the latest-battle divider to resize those
panes. Their positions persist locally. Previous Game Screen, Host Health, and
the latest-battle summary can be collapsed independently. The battle-history
window has a separate draggable divider between its battle list and
selected-battle report. Data-grid columns remain directly resizable as well.

The status strip distinguishes effective **Automation** action authority from
**Game Screen**, the observer's latest detected game context. For example, a
normal active run displays `Automation Enabled` and `Active Battle` rather than
two unqualified `RUNNING` values. Wave and Coins/min remain prominent. Service and
PID evidence remains available on Process without occupying the always-visible
status strip, and Previous Game Screen remains visible by default but can be
collapsed.

A fresh active running-battle Strategy Action Gate appears in its own amber
banner: **Strategy actions blocked — observation and safe collectors remain
active.** The banner also shows the operator-facing reason, failed checks, and
collectors that currently retain authority. It does not change the Automation
field, Pause selection, or global status color; those continue to represent
only requested and acknowledged control state. Missing, stale, inactive, or
wrong-runtime gate evidence is not displayed as an active gate. This structured
presentation requires Linux revision 22 and `strategy_action_gate_v1`.

The Automation Control panel uses selection highlights for requested and
acknowledged authority. **Automation Paused** means zero automated device input
while observation may continue. **Automation Enabled** permits guarded actions;
it does not claim the game is Running. **When this battle ends** separately
selects Continue automatically, Wait, or Return/stay Home; those choices never
act as an immediate Start/Resume command. The buttons apply immediately, which
prevents a periodic status refresh from replacing an unsaved selection.
State and terminal-policy acknowledgements match an exact Linux request ID;
same-value requests remain visibly pending without being rewritten.

**Start Battle** is enabled only for fresh verified Home New Battle evidence;
**Attach to Battle** is enabled only for fresh active or resumable evidence.
Linux revalidates the exact runtime, target, activity scope, and boundary and
reports unavailable, requested, awaiting-enable, acknowledged, rejected, or
interrupted state. A verified Home tap appears as `action_dispatched` and keeps
unrelated automation suppressed until battle adoption or a bounded failure.
**Take Manual Control** first obtains an acknowledged
indefinite Pause. **Return Control** stays Paused until an explicit Enable and
exclusive reconciliation complete. Attach and running Return use a newly
forced exact-target save, same-round identity, and final activity scope before
any unresolved allowlisted configuration UI can open. A post-background
restoration or authority loss leaves Automation Paused. Return is unavailable
at Tournament Results, unknown state, or without exact target/scope binding
rather than advertising an incomplete workflow. A failed Home New refresh is
recorded once as failed/interrupted and does not repeat background/foreground
input on later status frames.
A Home UI repair that exhausts after a successful save appears as
`awaiting_manual_correction` with the failed check and reason. Automation stays
Paused; after making that manual correction, Enable requests a new save rather
than replaying the retained receipt. Pause, Stop, or Take Manual Control during
Home setup yields before cleanup input, and only the same original workflow
may restore Home on a later Enabled observation.

The manual Surrender selector belongs to Take Manual Control. The default
excludes manual Surrender stats and writes only a save-backed nonrepresentative
record; the opt-in choice performs full terminal collection. Neither choice
authorizes automation to Surrender. A Strategy repair Surrender is available
only as the runtime's exact one-shot gate option for the current battle and
reason.

At Tournament Results, Wait retains the captured screen. Continue
automatically and Return/stay Home use the verified dismissal owner after the
result is saved; dismissal itself does not start another battle.

The strategy dropdown likewise preserves an unsent choice across refreshes.
For an active process, selection alone does not
change the current or queued strategy: choose **Use next battle** to leave the
current battle's strategy in place, or **Switch this battle** to request
adoption after fresh running or resumable-Home evidence. For a stopped
process, **Start Automation** saves and launches the visibly selected Strategy,
then leaves input Paused with no battle workflow selected. **Save startup
default** persists a stopped selection without starting. Adoption changes normal strategy behavior and
Battle End identity without a restart, while new-run initialization, session
preflight, and Home-only gates wait for the next genuine boundary. Selecting
the displayed Current strategy and queueing it cancels a different pending
request. Actions that would be no-ops are disabled; the panel reports request
acceptance immediately and shows selected, current, and pending values
separately.

The same panel selects a persistent numeric game-speed target. The dropdown
offers `x0.0` through `x6.0` in `x0.5` increments and `x6.3 — Maximum
available`. Lower values are exact targets for the current and later battles.
Maximum available actively verifies the visible `+` ceiling, accepting `x5.0`
only after a no-change probe proves the perk is absent and advancing to `x6.3`
when it is present. A custom target remains visibly warned, a managed Start
asks for confirmation, and an amber border means a live process has not
acknowledged the new target. Completed battle settings show the selected target
and any per-battle target changes. Selecting a different value during
`RUNNING` tells automation to enforce it immediately; changing speed directly
in the game is treated as drift and will be corrected. The status strip and
the helper below the dropdown separately show **Observed Speed**, read from the
same periodic status screenshot. Coins/min samples retain that observed speed,
which makes a deliberate mid-run change identifiable by time and approximate
wave instead of blending it invisibly into the battle.

Every explicit Tournament selection or Start with Tournament selected creates
one durable validation request. The panel reports Home preflight, ownership of
the one ordinary New Battle used for battle-only checks, cleanup, and the
terminal readiness or failure reason. Validation itself never enters or starts
the Tournament. If the runtime observes that a Tournament is already running,
it cancels the unclaimed request rather than carrying validation past that
battle; Tournament Results repeats the same fail-safe. Once ready, the app
opens **Tournament is ready** and reminds you to set Target Priorities for the
current Tournament Battle Conditions when the battle begins. Target Priorities
are not yet inspected or changed automatically.

**Start Tournament** performs lightweight current-receipt, configuration,
runtime, and screen checks, then authorizes one verified Tournament launch; it
does not rerun validation. **Cancel launch** consumes only the automatic launch
offer, so you can still start manually or explicitly select Tournament again
for fresh validation. **Decide later** leaves the offer pending under **Review
Tournament launch**. The real Tournament's first automation phase maxes EHLS
and EALS. A process restart cannot replay or Surrender a validation battle or
continue a launch owned by the former runtime.

**Configure run...** is an optional pre-start dialog populated from the
selected strategy's declared checks. Check a requirement to skip it once, or
leave every item unchecked to retain the complete strategy defaults. Saving
only stages the configuration; it does not start automation. The dialog never
opens automatically. Pause a live runtime before configuring it. Staged skips
are displayed under the button, are consumed by the next applicable run, and
are cleared if the selected strategy changes.

**Strategy profiles...** opens the shared Strategy Authoring shell. Linux
server revision 30 preserves `strategy_authoring_v1`,
`strategy_authoring_specialized_editors_v1`,
`strategy_authoring_profile_lifecycle_v1`, `strategy_action_gate_v1`, and every
older capability, retains `strategy_revision_history_v1`, and adds
`save_backed_setup_capture_v2` while retaining
`save_backed_setup_capture_v1`; revision 25 added
`managed_custom_module_presets_v1` after revision 24 added
`strategy_authoring_local_loadout_editors_v1`. It provides separate **Bases**
and **Strategies** catalogs plus immutable custom-Strategy History while
retaining the older latest-only profile endpoint for older clients. A Base is
a sparse reusable component and is never activatable. Editing one publishes the
next immutable revision. Strategies already pinned to an earlier revision
continue to use their embedded snapshot.

**Capture current setup as…** is a separate control for verified Home New,
Home Resume, or active-battle evidence while Automation Enabled. Linux requests
a new guarded serialization and shows captured values, explicit unresolved
rows, and an optional comparison Base. Save creates either a new immutable
Module preset or an unpublished custom Strategy draft and never selects,
queues, activates, applies, or publishes it. The Base is comparison-only. A
saved Strategy draft appears under **Captured Drafts** in Strategy Authoring;
select it to reopen the normal editable source together with that draft's own
captured origin, captured-versus-Base difference, and unresolved rows, then use
ordinary Validate → Review → Publish authority. Equivalent preset/local
definitions and set-ordered Guardian/Perk values are shown as provenance-only,
not effective setup changes. Paused capture reports that refresh is blocked and
does not use cached evidence. If active-battle Return Control already Paused on
a trusted mismatch after its own exact forced save, the same button may review
that retained process-local acquisition without new device input. Saving still
leaves Return Control Paused and unresolved. With source restoration proved,
an unavailable capture preserves prior action authority; an active-battle
identity contradiction raises a Strategy Gate, while an unsafe source return
or proved Home New contradiction persists Pause. Ready/terminal receipt-write
failure retries only the retained result without a second serialization or an
authority change. Opening a terminal capture is inspect-only; select **Try
capture again** to request another refresh. The native capture button remains
disabled when revision or capability compatibility fails, even if a stale
payload says `ready`.

Settings are grouped by the server registry. **Show active only** keeps the
normal view compact, while **Show all settings** exposes omitted settings.
Every row shows its source state, effective policy/value, provenance, and the
registry's observation and repair capabilities. Base rows offer **Not
Included**, **Included Enforce**, and **Included Observe** where allowed.
Strategy rows offer **Inherit**, **Override Enforce**, **Override Observe**,
and explicit **Ignore** where allowed; each local Strategy directive also has
**Reset to inherited**.

Every currently registered value type has a managed editor or an explicit
fixed presentation. Preset and Perk choices, initial values, structured fields,
list limits, dependencies, and toggle restrictions all come from Linux. Damage
Slider remains server-normalized percentage text. Card recharge mode rows
require one managed mode per declared Card. Free Upgrade locks retain their
exact three-item membership but allow the supported inspection order to move;
Guardian chips are shown as the fixed exact set because their source order has
no runtime meaning. Perk controls prevent duplicates, enforce declared limits,
and expose ordering only where it matters.

Modules, Target Priority, and Orb Distance can each use a shared preset or a
profile-local definition. The registry retains its revision-23 preset field and
adds one nested versioned local-editor contract. Modules renders every
server-declared slot with only that slot family's server-declared module
choices and prevents one module from occupying two slots. Target Priority
renders the complete unique server membership and allows only reordering. Orb
Distance renders exactly the server-declared Attack Range basis, Extra Orb,
and Workshop text fields; the client submits their text to Linux without
duplicating its distance parser or canonicalizer. The inactive preset and
local drafts both survive form changes, Base omission/reinclusion, Strategy
Inherit/Override/Ignore changes, and validation refreshes.

Whenever the Module shared-preset form is selected, the editor shows the
Linux-supplied normalized eight-slot definition with each slot label and
assigned Module. Bundled presets are labelled read-only; custom presets are
labelled immutable and save-as-new. **Create variant...** copies any selected
bundled or custom preset to a new safe ID, while **Save as preset...** submits
the current profile-local Module fields. Linux validates both through the
authoritative Module normalizer and stores custom presets only under its fixed
installation-local catalog. The client never supplies a path or duplicates
family validation.

After creation, the row refreshes its preset options without resetting the
collection, explicitly selects the new preset, and keeps the local draft
dormant. Validate → Review → Publish is still required; creating a preset never
publishes, selects, or activates a Base or Strategy. Collision or validation
failure leaves the current form, selections, and draft open. These controls are
hidden if managed preset creation is not advertised.

Ultimate Weapons use managed group, toggle, and on/off controls. Poison Swamp
stun is intentionally fixed to **Off**, the only value currently accepted by
runtime authority. Unknown retained weapons and toggle fields are named as
retained and merged back unchanged rather than being silently deleted. Auto
Pick Perks is intentionally fixed to **Enabled**, and the Farm deck, Workshop,
and Bots values are fixed to **Farm**. No normalizer was widened to make a
control more permissive. The GUI does not expose raw JSON, generated rules,
executor actions, or arbitrary unchecked strings.

Changing a source state keeps its dormant managed value. A previously omitted
Base setting or Strategy override starts from the server-supplied valid initial
value; a Strategy can reset to inherited or select explicit Ignore only where
the registry allows it. Validation and resolution remain Linux-owned, and
validation refreshes effective values without discarding retained draft data.
Unsupported Strategy families such as Tournament and No Strategy remain
clearly read-only.

A new Strategy draft may initially pin a latest compatible Base. An editable
existing Strategy showing **No Base** may also choose its first compatible Base
without cloning or changing its ID. That selection exposes **Review Base
selection...**; publication remains disabled until Linux computes the complete
semantic diff and the operator accepts it. A published Strategy pinned behind
that Base's latest revision instead shows **Review Base update...**. Both
reviews report settings added/removed/changed, inherited effective changes,
local overrides that remain unchanged, explicit ignores that remain ignored,
and resulting dependency or builder errors. Accepting a valid review changes
only the open draft and binds later publication to that exact reviewed source.

**Validate draft** returns normalized source, resolution/provenance, rule count,
and fingerprints without writing a file or returning the expanded generated
plan. **Review & Publish...** repeats validation and summarizes source changes,
effective changes, validation, fingerprints, and rule count before asking for
confirmation. Base publication uses the latest Base fingerprint and Strategy
publication uses the source fingerprint, so a stale editor retains its draft
and must reload instead of overwriting newer work.

Publishing never selects or activates a Strategy. After publication, use the
normal strategy dropdown plus **Use next battle**, **Switch this battle**, or a
managed Start. Existing schema-1 and schema-2 custom publications are adopted
conservatively into immutable history without rewriting their latest facade;
schema-1 authoring conversion still does not infer inheritance. Local
definition snapshots, embedded Base resolution, and fingerprints remain
Linux-owned review evidence rather than editable fields. Shared presets remain
available. Tournament behavior, generated YAML rules, executor actions,
runtime strategy gates, and activation behavior remain outside this editor.

**History** opens a separate custom-Strategy lineage window. Active and retired
lineages remain discoverable, with immutable versions newest first. The list
shows publication time, current/historical/retired state, pinned Base, family
and Tier, server-owned publication origin and audit identity, rule count,
source/Base/resolution/plan/publication/revision fingerprints, and whether the
retained publication still validates under current trusted code. It never
shows a filesystem path, raw generated plan, or revision-delete action.

Selecting a revision asks Linux for the semantic comparison with the current
latest publication. The review covers source directives, inherited/effective
values, Base pin and embedded snapshot, local overrides, explicit Ignore
changes, generated-plan fingerprint/rule count, metadata-only changes, and
current validation errors. **Restore as new revision** remains disabled until a
successful review. Confirmation states that Linux will re-normalize and rebuild
the exact historical intent through current trusted code and publish it as the
lineage's next immutable latest version—not move or mutate the old revision.
Restore never selects or activates the Strategy, restarts automation, changes
Pause, or changes runtime control. A stale history/latest conflict preserves
the open authoring draft; success refreshes both history and latest catalogs.

For a custom Strategy, **Rename Strategy** selects the editable display-name
field; **Review & Publish...** applies the rename through the same Linux
validation, review, stale-fingerprint protection, and next-version publication
as every other edit. The stable lowercase ID does not change. **Delete
Strategy...** never hard-deletes a publication: after an explicit confirmation,
Linux moves its exact file into `config/strategies/custom/retired`, removes it
from active catalogs, and records an audit entry. Bundled Strategies cannot be
renamed or deleted. A stale editor or a Strategy still selected by the control
directive is refused, and deletion never changes selection or activation.
Retirement preserves the complete immutable lineage. Managed restoration uses
**History** and publishes a reviewed retained revision as the next version;
the `retired` archive is evidence rather than a competing rollback interface.

### Manual Windows Strategy Authoring smoke

The Linux build cannot detect all WPF runtime binding failures. Run this check
on Windows against a disposable repository/profile catalog, never the
operator's real `config/strategies/custom` directory:

Operator report, 2026-08-02: the available phase-three Windows runtime smoke
checks were completed with no blocking issue reported. This was not exhaustive
Windows validation and predates the profile-local loadout editors. The bounded
disposable-catalog checks below, including the new preset/local cases, remain
the next unchecked worker and canonical coverage when the relevant environment
is available. A 2026-08-03 attempt exposed a profile-local Module rendering
regression and stopped before validation or publication; the repaired package
still requires the complete visible smoke below.

1. Copy the repository to a temporary test root, empty only that copy's custom
   profile/Base and custom Module preset catalogs, and start the control-surface
   server with `--repository-root`, `--module-preset-directory`, plus control,
   log, history, and telemetry paths inside the same temporary root. Do not use
   process or activation endpoints.
2. Connect the native client to that server and open **Strategy profiles...**.
   Confirm the window renders immediately with no `TwoWay`/read-only-property
   binding exception. Select a bundled read-only Strategy and confirm disabled
   ComboBoxes retain dark chrome with muted light text, and that the disabled
   settings-filter RadioButton labels remain readable. Confirm editable
   selections and dropdown items also use light text on the dark-blue surface,
   including highlighted and selected items; compilation alone does not
   exercise these boundaries.
3. Create a disposable Base. With **Show all settings**, exercise fixed values,
   the true-only boolean, Card recharge choices, Free Upgrade reorder,
   fixed Guardian chips, Perk add/remove/limits/order, Ultimate Weapon
   groups/toggles (including fixed Poison Swamp stun), all shared presets,
   profile-local Modules with all eight slots/no duplicate, complete reordered
   Target Priority, all three Orb Distance fields, and Damage percentage.
   Select Farm Standard and Tournament Standard in turn; verify the preview
   visibly lists exactly eight named slot/Module pairs and labels both bundled
   and read-only. Create a variant of one bundled preset, verify it appears
   immediately with a custom immutable label and becomes the explicit current
   row selection, then cancel before publication. In local form, change one
   Module, use **Save as preset...**, and verify the second custom preset is
   immediately selectable while the local draft remains dormant. Retry an
   existing ID and one invalid ID; verify the useful error leaves the complete
   draft and every visible selection unchanged. Confirm no preset operation
   creates a Base/Strategy publication or changes selection/activation.
   After changing any Module choice, confirm all eight current ComboBox
   selections remain visibly populated and that the new value is unavailable
   in the other compatible slot.
   Switch every loadout repeatedly between preset and local, omit/reinclude it,
   include each omitted setting, validate, publish, close, reopen, and verify
   exact active/dormant values and provenance. Submit one malformed local value
   for each editor and verify Linux rejects it without a write.
4. Create a disposable Strategy pinned to that Base. Exercise every applicable
   editor again, plus Inherit, Override Enforce, Override Observe, explicit
   Ignore, Reset to inherited, preset/local switching, and both forms' dormant
   values across repeated state changes. Validate, publish, close, reopen, and
   verify exact round trips and Linux-owned definition snapshots in review.
   Use **Rename Strategy** to change its display name, review and publish, then
   reopen and verify the name and version advanced while its stable ID did not.
5. Open an editable disposable existing Strategy with **No Base** (including a
   copied schema-1 fixture). Select the disposable Base, confirm **Review Base
   selection...** appears and publication is blocked before review, inspect and
   accept the semantic review, validate, publish, and reopen. Verify the same
   Strategy ID now pins the reviewed Base revision and nothing was activated.
6. Seed one unknown Ultimate Weapon group and one unknown toggle field through
   the disposable server fixture. Change a known toggle, validate, publish,
   reopen, and verify both unknown values are unchanged and only described as
   retained—not exposed as raw JSON.
7. Publish a second disposable Base revision, open the Strategy's Base-update
   review, verify the semantic diff, accept it into the draft, validate and
   publish, then reopen and verify the reviewed pin and inherited values.
8. Publish at least two revisions of a disposable Strategy, then open
   **History**. Confirm newest-first ordering, exact fingerprints/Base pins,
   origin/audit and validation state, and current versus historical status.
   Review the older revision and verify the Linux-computed source, effective,
   Base, Ignore/override, plan, rule-count, metadata, and validation comparison.
   Cancel once and prove no catalog/control change. Reopen, confirm **Restore as
   new revision**, and verify the next logical version appears while the old
   revisions remain byte-for-byte retained. Confirm no Strategy was selected or
   activated. Repeat discovery after retirement and verify the retired lineage
   can be reviewed/restored. If a second client advances latest between review
   and confirmation, verify the conflict preserves the first window's draft.
9. Clone a second disposable custom Strategy solely for deletion. Select it for
   the next battle in the disposable control catalog without starting a
   process, then verify **Delete Strategy...** is refused and neither the file
   nor control directive changes. Select a bundled Strategy, retry deletion,
   decline once to prove cancellation, then confirm it. Verify the custom item
   leaves both new and legacy active catalogs, its exact publication appears in
   the disposable `retired` archive, and no process or Strategy was activated.
10. Throughout the run, verify Validate and restore preview write nothing,
   publishing/restoring never changes
   the selected/active Strategy, no activation prompt appears, and the real
   operator profile catalog and control state remain byte-for-byte untouched.

When a startup requirement fails, the runtime publishes the failed check,
expected value, and allowed responses. The app opens **Startup check needs a
decision** automatically; **Review preflight decision** reopens the current
request. **Apply choice** resolves only that request, while **Decide later**
leaves automation blocked without changing anything. **Retry check** captures
fresh evidence. A configured fallback or **Bypass only this check for this
run** waives only the displayed requirement for the current run; all unrelated
preflight checks still execute. The same pending decision is visible to the
browser and CLI because the Linux control file remains authoritative.

An attached Tournament mismatch uses the same dialog as a non-blocking
preflight warning. **Pause for manual changes** persists Pause without ending
the Tournament, **Retry the read-only check** captures fresh evidence, and
**Continue despite...** waives only the displayed mismatch for the current
run. **Decide later** leaves the warning pending while Tournament result
observation continues. The attached Tournament check remains strictly inside
the battle; it never uses Exit Battle → Go Home → Resume Battle. Its Home-only
Workshop preset is reported as deferred unless exact bound save evidence is
already available.

Recent Activity refreshes independently once per second, follows the newest
entry, and defaults to the concise `ACTION`, `RESULT`, `WARN`, `ERROR`, and
`FAIL` **Operational** levels. Periodic `STATUS` and general `INFO` entries stay
out of that narrative. **Current run** is the default activity scope. It
survives an automation stop/restart, and verified Home `NEW_BATTLE` preflight
replaces it so the Home setup and its battle remain together. The runtime
fingerprints the newest copied in-game Battle History report before launch and
compares it when attaching later. If a battle completed while automation was
stopped—even when the next battle was started manually—the changed report
automatically starts the correct Current run scope; an unchanged report
preserves the existing activity. This uses the existing activity API, so it
does not require a native-client rebuild. **All recent** restores the rolling
log tail. **Clear view** records a local cursor and hides only entries already
displayed; it never deletes or truncates Linux logs, and **Show
cleared** restores them. A new run or log rotation resets that local cutoff.

The live banner labels the latest status explicitly and gives the most recent
earlier distinct state its own visible row. Use **Status only** for complete
heartbeat history, **Diagnostics** for detector/input detail, or **All levels**
for the complete interleaved log; warning/error and individual-level filters
remain available. Browser fallback activity also defaults to the Operational
levels and shows the prior state transition in its Current Battle panel.
Battle/status loading therefore cannot delay the log display; live status and
completed-battle refreshes are also isolated from one another. Select one or
more rows and use
**Copy selected**, right-click **Copy selected**, or press **Ctrl+C** to copy
log-formatted lines. Automatic rendering holds the visible rows while a
selection exists and those entries remain in the current log tail. Copying or
clearing the selection resumes the live display; log rotation or tail expiry
clears a stale selection automatically so current activity cannot remain hidden.
Bundled Perk results use familiar community aliases such as **PWR**, **CTO**,
**RTO**, **GT**, **BH**, and **DW** in the compact row. Double-click a row to
expand its full log-formatted detail; a structured Perk bundle is shown as one
full-name item per line beside its alias. Double-click it again to collapse the
detail and resume live updates. Copying still uses the original complete log
message rather than the compact presentation.

The live banner shows the PID only when systemd or the active runtime lock
identifies a currently live process. The Runtime Evidence panel shows the
systemd MainPID, lock PID, lock/PID liveness, and whether the two identities
agree. Stale lock metadata is retained for diagnosis but is not promoted as a
live process PID.

The **HOST HEALTH** strip is measured locally on Windows once per second, even
when the SSH tunnel is down. It shows host CPU/memory/clock, combined BlueStacks
CPU and RAM, detected process count, and aggregate publication state. Hover over
the strip for BlueStacks I/O, sampler cost, last Linux acknowledgement, and
errors. Sampling uses native counters on a below-normal-priority worker; it does
not capture the screen or start PowerShell, WMI, `nvidia-smi`, or another
per-sample process.

The strip's second line reports busiest-engine host GPU utilization,
dedicated/shared adapter memory, BlueStacks GPU utilization/memory, and the top
competing GPU process. Its tooltip lists up to five non-BlueStacks competitors
with PID, average/maximum utilization, and memory. Collection uses one
persistent native Windows PDH query with reusable buffers; names reuse the
existing ten-second process discovery. Missing counters display as unavailable.
GPU temperature and clocks are not collected because the corresponding sensor
interfaces are vendor-specific. PresentMon frame telemetry remains a separate
future opt-in provider.

The compact **Pause sampling** control remains visible in the health strip at
the window's minimum supported size. Pausing flushes the current partial
aggregate and stops new samples, while the independent uploader continues
draining queued telemetry. The health state changes to **Sampling paused** and
the tooltip retains the last sample time. **Resume sampling** continues the
same host/session sequence with an explicit UTC gap. This preference is saved
locally across control-surface restarts and does not pause automation. The left
workspace panels retain independent scrollbars, with their minimum heights
balanced so every panel remains reachable at the minimum window height.

Raw samples remain in a two-minute memory ring. Approximately ten-second
aggregates are queued in
`%LOCALAPPDATA%\TheTower\host-performance-pending.jsonl` before upload, so an
API or tunnel outage does not discard recent telemetry. The bounded queue keeps
the newest nominal 24 hours and reconnects automatically. Linux stores
idempotent aggregates in `logs/host_performance.sqlite3` with sample-time host,
ADB-port, UTC, and fresh current-run correlation. This requires server revision
13 and capabilities `host_performance_telemetry_v1` and
`host_performance_gpu_v1`.

The Process Lifecycle panel also shows the managed localhost ADB port. While
automation is stopped, **Save** stores the value on Linux for the next managed
start. While a live runtime has acknowledged indefinite **Pause**, the same
control becomes **Switch** and hands the existing process to the new target
without recreating its startup/session gates. Wait for target acknowledgement
before resuming; a failed connection or capture leaves the runtime paused on
its former target.

Attachment is never automatic. **Start Automation** changes only the managed
process lifecycle and leaves action authority Paused. After a fresh
observation, use the separately available **Start Battle** or **Attach to
Battle** control. The first requires verified Home **New Battle** and runs
normal gates. The second requires a verified active battle or Home **Resume
Battle**, preserves the requested battle identity, and must complete save-
backed validation before any unresolved allowlisted configuration UI may open.
A mismatched intent is rejected rather than converted to the other workflow.
The old Validate/Skip attachment radio buttons and attached-reload action are
not part of revision 29.

The managed runtime ADB port, bundled strategy, and startup-gate policy share
the Linux environment file while remaining independent settings. The Process
tab's ADB port selects the Linux runtime target. The Setup tab's Windows
BlueStacks and Linux ADB-forward ports configure transport. Normally the
managed runtime port matches the Linux ADB-forward port, but changing either
setting does not silently rewrite the other or alter the API tunnel.

The companion reports API SSH state independently of the GUI's HTTP probe. If
OpenSSH remains alive but that probe fails, the top bar keeps API SSH active,
labels HTTP unavailable, and keeps **Stop API tunnel** enabled.

The status endpoint advertises its API version, monotonic server revision, and
supported capabilities. The Windows build carries an expected API version, a
minimum server revision, and required capabilities. Any mismatch produces a
prominent full-width **Linux API update required** banner, disables dependent
actions, and gives disabled Start buttons the same blocker in a tooltip. The
current Better Control Model build requires revision 30,
`better_control_model_v2`, `save_backed_setup_capture_v2`,
`terminal_dispositions_v2`,
`managed_custom_module_presets_v1`,
`strategy_authoring_local_loadout_editors_v1`, and
`strategy_revision_history_v1` while retaining all earlier required
capabilities. The banner reports the actual revision/capability mismatch and
the exact recovery
sequence instead of relying on the smaller compatibility detail in the
scrollable SSH panel. The decision is not tied to the strategy feature that
first exposed the stale service problem. A future Windows feature that depends
on new Linux behavior must advance the server revision and the client's
minimum revision together.

Opening or connecting the Windows app never restarts Linux automatically. It
queries the fixed API unit over SSH on a bounded interval, independently of the
HTTP endpoint, so a deliberately stopped service is reported as stopped rather
than as an unexplained HTTP failure. The always-visible controls run only fixed
`systemctl --user start|stop|restart thetower-control-surface.service`
operations against the validated SSH destination. Stop and restart require
confirmation. Starting or restarting waits for the HTTP API to return and
verifies the complete compatibility contract; an incompatible service still
shows the full-width recovery banner. These operations do not install an
update, choose another command or service, restart main automation, alter the
active battle, or change either SSH tunnel. If the compatibility banner
remains, update the Linux checkout and restart the API service again.

## Windows-only lifecycle validation

Linux cross-publishing and the automated protocol/core tests cannot execute a
WPF session, Windows OpenSSH, named-pipe access-token enforcement, Job Object
inheritance, or user logoff. Before treating a new package as deployed, perform
this bounded validation on Windows with passwordless SSH and host-key trust
already configured:

1. Publish or copy the complete directory and confirm both executables are
   adjacent. Launch the GUI with no existing host; verify saved fields load but
   neither tunnel starts until its explicit Start action.
2. Start API and ADB tunnels independently. Record the companion PID and both
   SSH PIDs/endpoints from Setup, close the GUI, and verify both forwards remain
   usable. Reopen the GUI and verify it recovers desire, observed state, PID,
   endpoint, retry/conflict state, and diagnostics without starting replacements.
3. Stop and restart each tunnel separately. Create an occupied Linux ADB bind,
   confirm only ADB enters Conflict with raw SSH detail and no automatic retry,
   and confirm API SSH plus HTTP remain unaffected. Correct the port and use
   Retry/Restart; verify 5/10/20/30-second bounded-backoff presentation for an
   ordinary disconnect and that Stop cancels it.
4. Query and Start/Stop/Restart only
   `thetower-control-surface.service`; verify the four top-bar signals remain
   distinct and main automation is untouched.
5. With both tunnels desired, terminate the displayed companion PID from a
   separate PowerShell session. Verify its two owned `ssh.exe` children also
   exit, no pre-existing unrelated SSH process is adopted or stopped, and a new
   GUI/host does not replay either tunnel.
6. Exercise **Restart tunnel host...** for a compatible build and, using two
   deliberately different protocol builds, the mismatch path. Confirm the
   warning is explicit, replacement validates the companion identity, both SSH
   children stop, and the replacement starts with desires off.
7. With no desired tunnel, close the GUI and confirm the companion exits after
   about 15 seconds. Then start a desired tunnel, sign out of Windows, sign back
   in, and confirm neither the host nor a tunnel starts automatically.

The Linux API and fixed systemd user units must be installed first; see
[`../../deploy/systemd/README.md`](../../deploy/systemd/README.md).
