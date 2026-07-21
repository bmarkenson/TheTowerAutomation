# Runtime Operations

This runbook records stable operational facts for TheTower. Process IDs, waves,
screen states, control directives, and active targets are volatile and must be
re-inspected at the start of every thread.

## Python and repository

- Repository: `/home/brianm/dev/python/TheTower`
- Use `.venv/bin/python`; do not fall back to the system interpreter.
- Run tests as `.venv/bin/python -m pytest`.
- A broken virtual-environment dependency should be repaired rather than
  bypassed with a different interpreter or user-level package installation.

## ADB access

The usual BlueStacks target is `localhost:5555`, but confirm it from the active
lock/process/handoff before acting. Use bounded commands so a disconnected
target cannot stall a development turn:

```bash
timeout 8s adb -s localhost:5555 get-state
timeout 10s adb -s localhost:5555 exec-out screencap -p > /tmp/thetower_current.png
```

The expected connection response is `device`. The trusted project's
`.codex/config.toml` enables network access in the normal `workspace-write`
sandbox so it can reach the established host ADB server. Start new Codex
sessions after changing that configuration so the project layer is reloaded.

Run the bounded ADB check once through the normal workspace sandbox; do not
preflight it with a separate known-failing invocation. If a Codex surface does
not load the project configuration, its isolated command may try to start a
second ADB server and fail with an error such as `could not install
*smartsocket* listener: Operation not permitted`. Retry that command through
the approved host execution path. The isolated failure describes the
invocation environment; it does **not** prove that the established host ADB
server or emulator target is unavailable.

Project screenshot helpers accept native portrait framebuffers at `1080x1920`
or `720x1280` and normalize them into the canonical `1080x1920` vision space.
Runtime taps and swipes are converted back into the observed native geometry at
the centralized input boundary. Other sizes and majority-black decoded frames
are rejected. An incomplete compositor frame triggers one immediate fresh
capture; if that capture is also incomplete, callers receive no frame. State
detection and visible-control action matching independently reject incomplete
frames, so a preserved template strip cannot acquire action authority. Raw
manual `adb` commands do not provide these guards or coordinate conversion.

Project code should use `core.adb_utils` so device selection and shell behavior
remain centralized. The Stats clipboard report is read with Android's clipboard
service; its working lower-level command is:

```bash
adb -s localhost:5555 shell service call clipboard 3 s16 com.android.shell
```

## Mandatory runtime inspection

Inspect these sources together; none is sufficient alone:

```bash
.venv/bin/python tools/automation_ctl.py status
sed -n '1,160p' logs/automation_ctl.json
sed -n '1,160p' logs/automation-localhost_5555.lock
tail -120 logs/actions.log
timeout 8s adb -s localhost:5555 get-state
```

- The control file is persistent operator intent.
- The lock records the last owner PID and target, but may be stale after a
  crash. Confirm its PID against the host process table before treating it as a
  live owner.
- `actions.log` records what the automation actually observed and dispatched.
  It retains both concise operator entries and paired diagnostic evidence;
  control-surface Recent Activity defaults to the operational levels, while
  `Diagnostics` or `All levels` exposes coordinates, detector state, retries,
  and other low-level detail.
- A fresh screenshot is authoritative for the visible UI and can disprove a
  stale wave/status hint.
- Never infer `RUNNING`, `PAUSED`, Game Over, or Home solely from a dated
  handoff.

## Pause, resume, and process replacement

Use the persistent control mechanism:

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/automation_ctl.py pause --minutes 15
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py resume
```

After writing `PAUSED`, wait until `actions.log` confirms that the live process
consumed it. Paused automation continues capture, state detection, lifecycle
observation, and status reporting, while strategy and handler actions remain
blocked.

Before replacing a process, verify the current screen and allow any in-progress
guarded action to reach a safe boundary. Stop the known owner cleanly, confirm
its exit, and start the replacement under a persisted pause when validation is
needed before actions resume. Do not kill an arbitrary PID merely because it
appears in a possibly stale lock.

A shell launch returning success is not evidence that a replacement survived
the execution wrapper. Confirm the new host PID, refreshed lock metadata,
startup log, control consumption, and first state report together before
treating the replacement as live.

The Game Over handler polls the same control file while waiting. `PAUSED`
blocks Retry/Home, `STOPPED` exits without a terminal tap, and `WAIT` continues
to wait for an explicit mode change.

### Resolve a blocked startup gate

Before starting a run, the native app's optional **Configure run...** button
shows only the preflight checks declared by the selected strategy. Check any
requirement that should be skipped once, then save. Leaving the dialog unopened
or saving it with no checks selected uses every strategy default. The dialog
does not open automatically and does not start the run. When the automation
process is active, Pause it before opening or saving this configuration; this
prevents a run boundary from claiming the selections while the dialog is open.

The text equivalent is:

```bash
.venv/bin/python tools/automation_ctl.py configure-run
.venv/bin/python tools/automation_ctl.py configure-run skip bots_preset
.venv/bin/python tools/automation_ctl.py configure-run default bots_preset
```

The interactive form dynamically lists the selected strategy's checks and
toggles their one-run state. A staged skip is bound to that strategy, claimed
only at an applicable run boundary, and removed from the pending control record
when claimed. Its evidence remains attached to that run. Normal validation is
rearmed after Game Over. Changing the selected strategy clears staged skips so
an exception cannot silently carry into a different configuration.

When a startup check fails, the runtime publishes a decision containing the
specific failed requirement, the expected value, and its allowed choices. It
remains blocked until an operator retries the check or explicitly waives that
one requirement for the current run. The native Windows app and browser
fallback open a decision dialog automatically. Closing it with **Decide later**
leaves the gate pending and performs no action.

A direct interactive `main.py` launch presents the same choices in the
terminal. For a service or another non-interactive launch, inspect or resolve
the shared request with:

```bash
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py gate
.venv/bin/python tools/automation_ctl.py gate retry
```

`gate` without a choice prompts on stdin. `gate <choice-id>` is suitable for a
script or remote shell. `force-continue` remains only as a compatibility alias
for `gate bypass_once`; it cannot create an exception before a real failure and
it no longer skips the complete preflight.

Every decision is requirement-scoped. For example, accepting the configured
Flame fallback waives only `bots_preset`; Workshop locks, Modules, Cards,
Guardian Chips, Ultimate Weapons, and Auto Pick Perks still run normally. A
retry or waiver captures fresh evidence and resumes at the appropriate setup
boundary. The decision, selected option, fallback value, and observed failure
remain in the shared control record, while the in-memory waiver is cleared at
the run boundary.

Profiles can add named choices under `gate_fallbacks`. The Farm profile
currently offers **Continue with Flame for this run** for `bots_preset`:

```yaml
gate_fallbacks:
  bots_preset:
    - id: flame
      label: Continue with Flame for this run
      value: Flame
      description: Keep Flame and waive only the Farm Bot preset check.
```

Configured choices are displayed alongside the universal **Retry check** and
**Bypass only this check for this run** choices. Fallbacks must be declared for
a known preflight check and are copied into the generated strategy plan.

### Native Windows control surface

Install the checked-in user units once. They assume the repository is at
`~/dev/python/TheTower`; edit the copied units if it is elsewhere:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/thetower-automation.service ~/.config/systemd/user/
cp deploy/systemd/thetower-control-surface.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now thetower-control-surface.service
```

Do not enable `thetower-automation.service` unless automation should launch
automatically at Linux login. The control surface can start that fixed service
paused or running and can completely stop it. Stop persists `STOPPED` before
systemd signals the process; start persists `PAUSED` until systemd reports the
unit active.

The Windows controls can select the localhost ADB port, bundled strategy, and
startup-gate policy used by the next managed start. While automation is
stopped, select the new values and then start paused or running. The API
persists the validated settings in
`~/.config/thetower/automation-adb.env`; the systemd unit reads that file at
start. An absent file defaults to port `5555`, strategy `farm`, and immediate
startup gates. Explicit manual `main.py --adb-port PORT --strategy NAME
--startup-gates POLICY` arguments still win.

When replacing automation during a battle, select **Attach to current battle;
run gates next battle** before starting. The new process observes and controls
that existing run normally but suppresses only rules tagged as run
initialization or session preflight. The suppression survives transient
Unknown screens and Home `RESUME_BATTLE`. It ends only at Game Over, Tournament
Results, or verified Home `NEW_BATTLE`; the following battle then performs the
real gates. Do not select this for a process that is expected to configure a
newly started battle immediately.

The ADB port can also move without replacing a live automation process or
rerunning its in-memory startup/session gates:

1. Select indefinite **Pause** and wait until the GUI shows the runtime
   acknowledgement. A timed pause is not accepted for a target handoff.
2. Move the emulator, enter its new localhost ADB port, and select **Switch**.
3. Wait until runtime evidence shows the new active target and the handoff is
   acknowledged.
4. Verify the fresh observed screen, then select **Resume** when appropriate.

The runtime acquires ownership of the new target before attempting `adb
connect`, accepts it only after a supported screenshot succeeds, and releases
the old target lock only after that validation. A failed handoff remains paused
and retains the old target.

Bundled strategy selection also works without replacing an active process.
The GUI reports whether the request was accepted and shows current and pending
strategies independently:

1. Select `farm_t18`, `farm_t19_experiment`, `tournament`, or `none`.
2. During a battle, the request is queued for the next authoritative boundary;
   selecting another strategy replaces it. Select the displayed current
   strategy to cancel a different pending selection.
3. The existing strategy remains responsible for the completed battle record
   and its Game Over hook. The queued strategy is applied before terminal
   navigation or the next run's first actionable observation.
4. At verified Home **New Battle** or Workshop, the request applies immediately,
   including while Pause blocks runtime actions. Home **Resume Battle** is not
   a boundary and leaves the request pending.

The same selection is persisted in the managed environment so a process
restart cannot silently restore the old strategy. A strategy acknowledgement,
not the accepted API response alone, is evidence that the live runtime applied
the request.

The API also verifies that the installed unit advertises this file through
systemd. If it reports that the unit does not load the file, copy the current
checked-in automation unit over the installed user unit and run
`systemctl --user daemon-reload` before starting automation. At runtime, the
automation attempts `adb connect localhost:PORT` before its first capture and
retries a disconnected target without changing the configured port.

Build the native WPF application on Windows with the .NET 8 SDK by running
`windows\TheTower.ControlSurface\publish.ps1`, or cross-publish the same
`win-x64` target from Linux with
`windows/TheTower.ControlSurface/publish-linux.sh` after following the
[Windows client README](../windows/TheTower.ControlSurface/README.md#publish).
Linux can compile the Windows-targeted project but cannot run its WPF UI. The
self-contained single-file output is
`windows\TheTower.ControlSurface\publish\win-x64\TheTower.ControlSurface.exe`.

The app can own the SSH tunnel itself. Enter the Linux SSH destination
(`host`, SSH config alias, or `user@host`), keep local and remote ports at 8787,
and select **Start tunnel**. It launches Windows `ssh.exe` in BatchMode with
forward-failure and keepalive checks, then connects the API to
`http://127.0.0.1:8787`. Establish host-key trust once from PowerShell before
using the non-interactive app tunnel:

```powershell
ssh <linux-user>@<linux-host>
```

The application uses the same persistent control file as
`tools/automation_ctl.py`. The selected state and Game Over mode are visibly
highlighted; amber indicates that a live runtime has not acknowledged a new
directive yet. It also shows runtime evidence, independently refreshed and
level-filterable recent activity, and
unified Battle/Tournament records; filters by type, Tier, waves, strategy, and
quality; and displays Coins/hour, Cells/hour, captured perks, resolved settings,
and preflight evidence. A directive is shown separately from its runtime
acknowledgement; wait for acknowledgement before assuming a live process is
paused or resumed.

The Linux service still serves the browser client at
`http://127.0.0.1:8787/` as a fallback. For a manual tunnel:

```powershell
ssh -N -L 8787:127.0.0.1:8787 <linux-user>@<linux-host>
```

The loopback listener plus SSH tunnel is the recommended transport. A direct
non-loopback bind requires a bearer token in `THETOWER_CONTROL_TOKEN`, but the
built-in server is plain HTTP and should not be exposed to an untrusted LAN.
See [`architecture/control_surface.md`](architecture/control_surface.md) for
the API, authority boundaries, and planned capabilities.

## Live-action authority

- Never Surrender a pre-existing or operator-owned battle merely to create a
  development test boundary. A bounded developer-owned battle may be
  Surrendered only after the task author has authorized it and ownership was
  recorded before the battle started.
- Runtime automation may Surrender an active strategy-owned battle only when a
  profile-declared gate has authoritative mismatch evidence for settings that
  cannot be changed during battle. The same guarded workflow must own Game Over
  → Home, correction, restart, and fresh revalidation. Uncertain evidence and
  failures outside that repair class remain blocked without Surrender.
- A profile-declared setting may be repaired in battle only through an explicit
  safe-transition contract. Poison Swamp Stun `on` → `off` requires a freshly
  detected Poison Swamp tile, authoritative detail and checked-control matches,
  verification of `off`, and a verified return to `RUNNING/UW_MENU`. Damage
  Slider enforcement requires `RUNNING/ATTACK_MENU`, authoritative panel/mode/
  OCR evidence before every explicit arrow tap, strict progress, final-value
  verification, and a verified return to `RUNNING/ATTACK_MENU`. Neither path
  authorizes Surrender or Home traversal.
- Safe live validation, verified taps, resumable Exit Battle → Go Home
  traversal, and process restarts are allowed only within the user's stated
  task scope.
- Use fresh source-state evidence immediately before a tap. Transition frames
  and old screenshots do not carry action authority.
- If manual activity or unexpected navigation is visible, pause rather than
  racing the player.
- A natural Game Over is a valuable lifecycle boundary. Preserve it long enough
  to capture evidence or change control state safely; do not manufacture one.
- `--fast-game-over` intentionally suppresses record capture and should be used
  only for a known already-recorded terminal screen. Restart normally afterward
  so future battles remain capture-enabled.

## Evidence and records

- Runtime actions and state: `logs/actions.log`
- Persistent control: `logs/automation_ctl.json`
- Per-battle records: `logs/battles/Battle*.json` and `Battle*.md`
- Tournament records: `logs/tournaments/Tournament*.json` and `Tournament*.md`
- During-run Coins/min progression: embedded numeric samples in the applicable
  completed record (not a separate CSV or screenshot series)
- Failure/OCR evidence: `screenshots/matches/`
- Canonical regression fixtures: `test/fixtures/`
- Actionable backlog: `PENDING_DEVELOPMENT.md`
- Open anomalies: `docs/observed_issues.md`
- Resolved recurrence history: `docs/issues/`

`logs/` and `screenshots/` are ignored runtime evidence, not substitutes for a
tracked issue entry. When an anomaly matters beyond the current thread, record
its date, symptom, evidence, status, and fix/test linkage in the issue ledger.
