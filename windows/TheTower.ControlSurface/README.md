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

The app can start and stop the recommended SSH tunnel itself. Enter the Linux
destination as an SSH config alias, host, or `user@host`; leave both ports at
8787; and select **Start tunnel**. It uses the Windows OpenSSH Client with
BatchMode, keepalives, and `ExitOnForwardFailure`, and stops its tunnel when the
application exits.

Passwordless public-key authentication must already work, and the host key
must already be trusted. The one-time interactive setup or manual equivalent is:

```powershell
ssh <linux-user>@<linux-host>
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
```

Starting the in-app tunnel automatically selects
`http://127.0.0.1:<local-port>` and connects. The standalone **Connect** button
supports an already-running manual tunnel or an authenticated TLS reverse
proxy. The application persists the URL, SSH destination, and port preferences,
but the bearer token is held only in memory and is never saved. The saved tunnel
is not started automatically when the application opens; select **Start tunnel**
after launch.

The main and Battle History windows also remember their normal position, size,
and maximized state in `%LOCALAPPDATA%\TheTower\control-surface.json`. A saved
position that no longer leaves a usable title bar on the current virtual desktop
is ignored, and a window is never reopened minimized.

Only one control-surface process is allowed per Windows session. Launching the
application again restores and foregrounds the existing main window, or flashes
it on the taskbar if Windows declines the foreground request.

The operational window keeps the most recent completed battle visible without
devoting the normal control workspace to the full history. Select **Open battle
history...** to open the separate completed-battles window. That window merges
Battle and Tournament records and classifies Farm, Tournament, and Milestone
using strategy plus terminal-screen evidence. It filters by type, Tier, wave
range, strategy, and capture quality. The report banner includes Coins/hour and
Cells/hour, followed by complete Stats rows, Game Stats-only and derived values,
Coins/min progression, the captured perk order, resolved run settings, and
observed runtime/preflight evidence.
The battle list can export the currently filtered rows as an Excel-compatible
UTF-8 CSV without requesting any additional Linux-side authority.
The Strategy filter is an exact-match dropdown populated from the currently
loaded records. Periodic battle refreshes leave an unchanged list alone and
defer genuine updates while a Type, Strategy, or Quality filter menu is open so
the popup and selected battle remain stable.

Drag the dividers in the operational window to resize the control column, SSH
tunnel, automation controls, process lifecycle, runtime evidence, latest battle,
and recent activity areas. The battle-history window has a separate draggable
divider between its battle list and selected-battle report. Data-grid columns
remain directly resizable as well.

The Automation Control panel uses selection highlights instead of permanently
colored Pause and Resume actions. Cyan is the saved state or Game Over mode;
amber means a live runtime has not acknowledged that directive yet. Mode buttons
apply immediately, which prevents a periodic status refresh from replacing an
unsaved combo-box selection. The strategy dropdown likewise preserves an
unsent choice across refreshes and does not apply it merely because it is
selected. Choose **Queue for next boundary** to leave the current battle's
strategy in place, or **Adopt for active battle** to request adoption after
fresh running or resumable-Home evidence. Adoption changes normal strategy
behavior and Battle End identity without a restart, while new-run
initialization, session preflight, and Home-only gates wait for the next
genuine boundary. Selecting the displayed Current strategy and queueing it
cancels a different pending request. Actions that would be no-ops are disabled;
the panel reports request acceptance immediately and shows selected, current,
and pending values separately.

**Configure run...** is an optional pre-start dialog populated from the
selected strategy's declared checks. Check a requirement to skip it once, or
leave every item unchecked to retain the complete strategy defaults. Saving
only stages the configuration; it does not start automation. The dialog never
opens automatically. Pause a live runtime before configuring it. Staged skips
are displayed under the button, are consumed by the next applicable run, and
are cleared if the selected strategy changes.

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
entry, and defaults to the concise **Operational** levels. Use **Diagnostics**
for detector/input detail or **All levels** for the complete interleaved log;
warning/error and individual-level filters remain available. Browser fallback
activity also defaults to the operational levels. Battle/status loading
therefore cannot delay the log display; live status and completed-battle
refreshes are also isolated from one another. Select one or more rows and use
**Copy selected**, right-click **Copy selected**, or press **Ctrl+C** to copy
log-formatted lines. Automatic rendering holds the visible rows while a
selection exists so an incoming refresh cannot clear it; copying or clearing
the selection resumes the live display.

The live banner shows the PID only when systemd or the active runtime lock
identifies a currently live process. The Runtime Evidence panel shows the
systemd MainPID, lock PID, lock/PID liveness, and whether the two identities
agree. Stale lock metadata is retained for diagnosis but is not promoted as a
live process PID.

The Process Lifecycle panel also shows the managed localhost ADB port. While
automation is stopped, **Save** stores the value on Linux for the next managed
start. While a live runtime has acknowledged indefinite **Pause**, the same
control becomes **Switch** and hands the existing process to the new target
without recreating its startup/session gates. Wait for target acknowledgement
before resuming; a failed connection or capture leaves the runtime paused on
its former target.

When starting automation in a battle that was already running, select
**Attach to current battle; run gates next battle** before **Start paused** or
**Start running**. Normal automation continues on that battle, but new-run
initialization and session-preflight rules remain suppressed until Game Over,
Tournament Results, or a verified Home **New Battle** boundary. The next battle
then performs those gates normally. Leave the option clear when the first
observed battle needs its startup gates immediately.

The Tournament observer is the deliberate exception to session-preflight
suppression: it performs its read-only check on the attached run so the warning
above can report mismatches without changing configuration or blocking natural
terminal capture.

The ADB port, bundled strategy, and startup-gate policy share the managed
Linux environment file while remaining independent settings. Changing the ADB
port changes the emulator target, not the SSH/API tunnel ports.

An in-app SSH process is reported as connected only after the forwarded Linux
status endpoint responds successfully. If OpenSSH remains alive but that probe
fails, the app labels the API unavailable and keeps **Stop tunnel** enabled.

The status endpoint advertises its API version, monotonic server revision, and
supported capabilities. The Windows build carries an expected API version, a
minimum server revision, and required capabilities. Any mismatch produces a
generic client/server compatibility warning and disables dependent actions;
the decision is not tied to the strategy feature that first exposed the stale
service problem. A future Windows feature that depends on new Linux behavior
must advance the server revision and the client's minimum revision together.

Opening or connecting the Windows app never restarts Linux automatically. For
an incompatible service, the app displays **Restart Linux control service**.
After confirmation, that button runs only the fixed
`systemctl --user restart thetower-control-surface.service` command against the
validated SSH destination, waits for the API to return, and verifies the full
compatibility contract before reporting success. It reloads the installed code
but does not install an update, choose another command or service, restart main
automation, or alter the active battle.

The Linux API and fixed systemd user units must be installed first; see
[`../../deploy/systemd/README.md`](../../deploy/systemd/README.md).
