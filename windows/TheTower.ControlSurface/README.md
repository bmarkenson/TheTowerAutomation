# Native Windows Control Surface

This WPF application is the primary desktop client for the Linux control
surface API. It has no browser dependency and can be published as a
self-contained, single-file Windows executable.

## Publish

Install the .NET 8 SDK on the Windows build machine, then run PowerShell from
this directory:

```powershell
.\publish.ps1
```

The executable is written to:

```text
publish\win-x64\TheTower.ControlSurface.exe
```

The target PC does not need the .NET runtime because the publish is
self-contained. The output directory is ignored by Git.

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
starts the build. The project explicitly enables Windows targeting; copy the
resulting `.exe` to Windows for runtime testing.

## Connect

The app manages two independently controlled Windows OpenSSH processes. The
API tunnel preserves the Windows-local forward from `127.0.0.1:8787` to the
Linux API at `127.0.0.1:8787`. The ADB reverse forward exposes the PC's
Windows-local BlueStacks listener through a configurable Linux loopback port:

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

Each process uses BatchMode, keepalives, and `ExitOnForwardFailure`. An ADB
remote-listener conflict therefore does not stop the API tunnel. The Setup tab
reports whether Windows has a TCP listener for the configured BlueStacks port
separately from whether OpenSSH accepted the Linux reverse listener. Raw SSH
exit detail is retained. A bind or SSH-policy conflict pauses automatic ADB
reconnect until the operator changes the port or policy and starts it again;
other unexpected ADB-tunnel exits retry after 5, 10, 20, then at most 30
seconds. **Stop ADB forward** cancels a pending retry. Both tunnel processes
stop when the application exits.

Passwordless public-key authentication must already work, and the host key
must already be trusted. The one-time interactive setup or manual equivalent is:

```powershell
ssh <linux-user>@<linux-host>
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
ssh -N -R 127.0.0.1:5555:127.0.0.1:5555 <linux-user>@<linux-host>
```

Starting the in-app tunnel automatically selects
`http://127.0.0.1:<local-port>` and connects. The standalone **Connect** button
supports an already-running manual tunnel or an authenticated TLS reverse
proxy. The application persists the URL, SSH destination, and port preferences,
but the bearer token is held only in memory and is never saved. Saved API and
ADB tunnel settings are not started automatically when the application opens;
select **Start API tunnel** and **Start ADB forward** as needed after launch.

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
scrolling cards. Everyday Pause, Resume, game-speed, Game Over, strategy, and
run-configuration actions remain on Controls. Service state, PID, ADB target,
Start, Reload, and Stop are on Process. API and SSH fields are confined to the
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

The status strip distinguishes **Automation**—the requested control
directive—from **Game Screen**, the observer's latest detected game context.
For example, a normal active run displays `Running` and `Battle` instead of two
unqualified `RUNNING` values. Wave and Coins/min remain prominent. Service and
PID evidence remains available on Process without occupying the always-visible
status strip, and Previous Game Screen remains visible by default but can be
collapsed.

The Automation Control panel uses selection highlights instead of permanently
colored Pause and Resume actions. Cyan is the saved state or Game Over mode;
amber means a live runtime has not acknowledged that directive yet. Mode buttons
apply immediately, which prevents a periodic status refresh from replacing an
unsaved combo-box selection. The strategy dropdown likewise preserves an
unsent choice across refreshes. For an active process, selection alone does not
change the current or queued strategy: choose **Use next battle** to leave the
current battle's strategy in place, or **Switch this battle** to request
adoption after fresh running or resumable-Home evidence. For a stopped
process, **Start paused** and **Start running** atomically save and launch the
strategy that is visibly selected, so an older next-start value cannot win the
process boundary. **Save startup default** is only needed to persist a stopped
selection without starting. Adoption changes normal strategy behavior and
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
the Tournament. Once ready, the app opens **Tournament is ready** and reminds
you to set Target Priorities for the current Tournament Battle Conditions when
the battle begins. Target Priorities are not yet inspected or changed
automatically.

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
server revision 19 and capability `strategy_authoring_v1` provide separate
**Bases** and **Strategies** catalogs while retaining the revision-18 profile
endpoint for older clients. A Base is a sparse reusable component and is never
activatable. Editing one publishes the next immutable revision; Strategies
already pinned to an earlier revision continue to use their embedded snapshot.

Settings are grouped by the server registry. **Show active only** keeps the
normal view compact, while **Show all settings** exposes omitted settings.
Every row shows its source state, effective policy/value, provenance, and the
registry's observation and repair capabilities. Base rows offer **Not
Included**, **Included Enforce**, and **Included Observe** where allowed.
Strategy rows offer **Inherit**, **Override Enforce**, **Override Observe**,
and explicit **Ignore** where allowed; each local Strategy directive also has
**Reset to inherited**.

Preset, percentage, boolean, Perk Ban, and ordered Auto Pick values use safe
managed controls. Complex registry values without a phase-two editor remain
visible and read-only. Their original JSON value is retained in the typed model
and round-trips unchanged through validation and publication; the GUI does not
offer raw generated rules, executor actions, or a general raw-value editor.
Unsupported Strategy families such as Tournament and No Strategy are clearly
read-only.

A new Strategy draft may initially pin a latest compatible Base. A published
Strategy pinned behind that Base's latest revision shows an update banner; its
pin cannot change directly. **Review Base update...** asks Linux to compute
settings added/removed/changed, inherited effective changes, local overrides
that remain unchanged, explicit ignores that remain ignored, and resulting
dependency or builder errors. Accepting a valid review changes only the open
draft and binds later publication to that exact reviewed source.

**Validate draft** returns normalized source, resolution/provenance, rule count,
and fingerprints without writing a file or returning the expanded generated
plan. **Review & Publish...** repeats validation and summarizes source changes,
effective changes, validation, fingerprints, and rule count before asking for
confirmation. Base publication uses the latest Base fingerprint and Strategy
publication uses the source fingerprint, so a stale editor retains its draft
and must reload instead of overwriting newer work.

Publishing never selects or activates a Strategy. After publication, use the
normal strategy dropdown plus **Use next battle**, **Switch this battle**, or a
managed Start. Existing schema-1 profiles are converted conservatively only in
memory when opened and are not rewritten unless explicitly published. The
remaining complex specialized value editors are a later phase; Tournament
behavior, generated YAML rules, executor actions, runtime strategy gates, and
activation behavior remain outside this editor.

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
observation continues.

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

Attachment is automatic when Start first finds an active battle or Home
**Resume Battle**. The Process tab offers two explicit choices:
**Validate current battle if attached** runs one read-only strategy check;
**Skip checks for current battle** suppresses all strategy setup checks for
that battle. A repairable validation mismatch offers **Restart battle and
repair setup**, but does not restart or change configuration unless the
operator chooses it. At verified Home **New Battle**, this choice is ignored
and normal pre-battle checks always run. Game Over, Tournament Results, or a
verified Home **New Battle** boundary clears the attached-battle choice so the
following battle performs complete gates. Both Start actions persist the
strategy currently visible in the Strategy dropdown before launching the Linux
process.

The managed runtime ADB port, bundled strategy, and startup-gate policy share
the Linux environment file while remaining independent settings. The Process
tab's ADB port selects the Linux runtime target. The Setup tab's Windows
BlueStacks and Linux ADB-forward ports configure transport. Normally the
managed runtime port matches the Linux ADB-forward port, but changing either
setting does not silently rewrite the other or alter the API tunnel.

An in-app SSH process is reported as connected only after the forwarded Linux
status endpoint responds successfully. If OpenSSH remains alive but that probe
fails, the app labels the API unavailable and keeps **Stop API tunnel** enabled.

The status endpoint advertises its API version, monotonic server revision, and
supported capabilities. The Windows build carries an expected API version, a
minimum server revision, and required capabilities. Any mismatch produces a
prominent full-width **Linux API update required** banner, disables dependent
actions, and gives disabled Start buttons the same blocker in a tooltip. The
banner reports the actual revision/capability mismatch and the exact recovery
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

The Linux API and fixed systemd user units must be installed first; see
[`../../deploy/systemd/README.md`](../../deploy/systemd/README.md).
