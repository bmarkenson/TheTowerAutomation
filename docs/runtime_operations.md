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
- `actions.log` records what the automation intended, observed, and dispatched.
  Guarded and multi-step workflows begin with a concise `ACTION` header that
  explains their purpose before the individual input records. The log retains
  both concise operator entries and paired diagnostic evidence; control-surface
  Recent Activity defaults to the operational levels, while `Diagnostics` or
  `All levels` exposes coordinates, detector state, retries, and other
  low-level detail.
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

Farm strategies declare their Home Perks configuration as semantic profile
data. The current Farm baseline bans **Cash Trade-Off**, **Enemies Damage /
Tower Damage Trade-Off**, **Lifesteal / Knockback Trade-Off**, **Interest**,
and **Defense Absolute**. Its Auto Pick priority is:

1. Perk Wave Requirement
2. Game Speed
3. Coin Trade-Off
4. Golden Tower Bonus
5. Black Hole Duration
6. Death Wave Quantity
7. Coins Bonus
8. Free Upgrade Chance
9. Orbs
10. Chain Lightning Damage
11. Inner Land Mines
12. Spotlight Damage
13. Damage

At verified Home `NEW_BATTLE`, setup opens the independently verified Perks
configuration control after returning from Cards. It reads the complete
selected Ban block and the strategy-sized Auto Pick prefix. A mismatch is
repaired before moving to the next tab. Extra bans are removed directly from
the fixed Selected Perks block; only missing required bans require an
Available-list checkbox search. Auto Pick uses the matched up arrow. Every
input reacquires the same semantic row, every move must make strict upward
progress, and completion requires an exact final list followed by closing
Perks and revalidating Home `NEW_BATTLE`. The complete Home setup synchronizes
persistent control before every tap and swipe. Pause waits without cleanup
input; after Resume it restores verified Home and restarts setup with fresh
evidence. A strategy that does not declare both lists cannot trigger these
changes. Uncertain OCR, an unavailable row, unchanged input, or an exhausted
move/scroll bound fails closed.

Every decision is requirement-scoped. For example, accepting the configured
Flame fallback waives only `bots_preset`; Workshop locks, Modules, Cards,
Guardian Chips, Ultimate Weapons, Auto Pick Perks, Perk Bans, and Auto Pick
priority still run normally. A retry or waiver captures fresh evidence and
resumes at the appropriate setup boundary. The decision, selected option,
fallback value, and observed failure remain in the shared control record,
while the in-memory waiver is cleared at the run boundary.

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
stopped, select the new values and then start paused or running. Each Start
request atomically persists the strategy currently visible in the client before
launching the service, so a stale saved strategy cannot bypass that selection's
Home-only gates. The API persists the validated settings in
`~/.config/thetower/automation-adb.env`; the systemd unit reads that file at
start. An absent file defaults to port `5555`, strategy `farm`, and immediate
startup gates. Explicit manual `main.py --adb-port PORT --strategy NAME
--startup-gates POLICY` arguments still win.

Selecting Tournament, or starting the stopped managed service with Tournament
selected, authorizes one validation run. The control file records a durable
request tied to that exact strategy request and generated-plan fingerprint.
After every declared Home check passes, the runtime atomically claims the
request and then taps only a freshly verified ordinary **New Battle** control.
It does not tap **Resume Battle**, enter the Tournament screen, or start a
Tournament during validation. In that disposable ordinary battle it leaves
Auto Perks unchanged, enforces Damage Slider `100%`, requires Attack Range
`98.38m`, enforces Orb Distance Extra `87.16m` / Workshop `80.37m`, and
verifies the configured Ultimate Weapons and Spotlight Missiles. Staged
one-run waivers are not claimed for this validation; a Home preflight failure
consumes the request and reports its failed check without starting a battle. A
conclusive in-battle pass or failure starts guarded cleanup: only the same
runtime/ADB owner may Surrender that battle and return from Game Over to
verified Home **New Battle**. The status panel then reports either readiness
and a Tournament-launch prompt or the validation failure reason.

Each later explicit Tournament selection or managed Start creates a new
request. Restarting an unattended runtime does not: a claimed, running, or
cleanup receipt owned by the former process is failed without a tap or
Surrender. A changed plan fingerprint likewise requires another explicit
selection.

After readiness, review the reminder to set Target Priorities to suit the
current Tournament Battle Conditions when the battle begins. Those controls
are in-battle. The prompt is a reminder only: automation does not yet inspect,
choose, or validate Target Priorities. Then choose one of:

- **Start Tournament** — authorize the current runtime to enter and start
  exactly one Tournament battle. This performs only lightweight checks that the
  ready receipt, configuration, runtime owner, `RUNNING` acknowledgement, and
  Home/Tournament-entry screen are still current; it does not rerun the Home or
  in-battle validation suite.
- **Cancel launch** — consume the automatic launch offer without invalidating
  the successful validation evidence. The operator may still start manually or
  explicitly select Tournament again to request a fresh validation.
- **Decide later** or close the prompt — leave the offer pending and reopen it
  later through **Review Tournament launch**.

For confirmed launch, the runtime atomically claims the receipt before input,
then uses only freshly verified **New Battle**, Tournament **Open**, and
Tournament **Battle** controls. It rechecks the same live owner, current
request, and Pause state before every tap. A timeout, restart, owner mismatch,
superseded request, unexpected battle, or ambiguous transition fails closed
without further input. Manually starting the Tournament while the offer is
pending is also supported and consumes the offer when the runtime observes the
fresh Tournament boundary.

The real Tournament's opening run-initialization gate maxes Enemy Health Level
Skip and Enemy Attack Level Skip before normal observer handling; the
disposable validation battle does not buy those upgrades or fabricate their
completion. Automation never Surrenders the real Tournament battle.

When replacing automation during a battle, select **Attach to current battle;
run gates next battle** before starting. The new process observes and controls
that existing run normally but suppresses only rules tagged as run
initialization or session preflight. The Tournament observer is the narrow
exception: it runs its declared preflight on attachment, enforces only Damage
Slider `100%`, the Range `98.38m` Orb Distance preset, and Poison Swamp Stun
`on`, and reports other bad settings without acquiring Home-repair authority.
The suppression survives transient Unknown screens and Home `RESUME_BATTLE`.
It ends only at Game Over, Tournament Results, or verified Home `NEW_BATTLE`;
the following battle then performs the real gates. Do not select this for a
process that is expected to configure a newly started battle immediately.

For a checked-in Python update during a running battle, prefer **Reload
automation for current battle** over a separate Stop/Start sequence. The
control surface performs one guarded replacement of
`thetower-automation.service` and therefore replaces its `main.py` process:

1. Persist an indefinite Pause and wait for the existing runtime to acknowledge
   it.
2. Require that same PID and ADB lock owner to publish a fresh post-request
   `RUNNING` observation. Capture and detection continue while paused; no
   handler or strategy action is allowed.
3. Stop the fixed automation unit, launch its replacement once with
   `startup_gates=next_run`, and immediately restore the configured policy for
   future ordinary starts.
4. Require a distinct systemd MainPID, matching held ADB lock, attached-policy
   startup log, Pause consumption, and first status observation from the
   replacement.
5. Restore the prior `RUNNING`, indefinite `PAUSED`, or unexpired timed-Pause
   intent. An expired timed Pause restores `RUNNING`.

Any failure after Pause preparation begins leaves control `PAUSED` and reports
the missing evidence; an initial owner/precondition rejection changes nothing.
A fresh non-running observation rejects the
replacement after Pause, before systemd is stopped. The operation does not
fabricate completed gate variables: attachment suppression ends at the normal
Game Over, Tournament Results, or verified Home `NEW_BATTLE` boundary. Process-
local histories such as Coins/min samples restart with the new process and
should be interpreted as since attachment. A raw `systemctl --user restart`
also replaces `main.py`, but does not provide this Pause, attachment-policy,
readiness, or state-restoration protocol.

If attached Tournament preflight finds an authoritative mismatch, the control
surface opens a non-blocking warning. **Pause for manual changes** persists
Pause without ending the run; **Retry** captures fresh validation evidence; and
**Continue observing** waives only the displayed mismatch for that run. Closing
the warning leaves it pending while natural Tournament Results/Game Over
capture remains active. If Pause is selected, terminal handling waits until the
operator resumes, as it does for every paused handler action.

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

### No Strategy run inventory

Use `none` when the purpose of a battle is to discover and preserve the actual
configuration rather than assert a known profile. `No Strategy` still runs the
normal general handlers, but it has no strategy upgrade actions or startup/
session gates. Its observer never fills a missing value from Farm,
Tournament, Tier, or another expected profile.

During the battle:

1. Start or attach the managed runtime with strategy `none`. If you want to
   navigate the game manually, Pause first; passive frame observation continues
   while paused.
2. Once `RUNNING` is visible, the runtime takes exclusive ownership of one
   guarded, read-only inventory pass. It opens and restores Cards, Perks,
   Ultimate Weapons, Modules, Event/Bots, Guild/Guardians, Target Priority, and
   the Damage Slider when Attack is accessible. Ultimate Weapon evidence is
   merged across the complete bounded scroll. Normal handlers do not race this
   traversal.
3. The purple sword badge beside Tier records `Attack Dissonance` identity.
   Tier by itself still does not identify a Farm, Milestone, or Dissonance run.
   On Attack Dissonance, the collector does not probe the disabled Attack menu;
   Damage Slider is recorded as unavailable with that reason.
4. Every tap requires the expected source state and every transition requires
   the expected destination. Pause is synchronized before each input. If Pause
   arrives mid-pass, no cleanup input is sent; after Resume, the collector first
   restores the known read-only screen and safely restarts the pass.

At natural Game Over, No Strategy always performs the full structured capture;
`--fast-game-over` does not suppress this inventory record. If mode is `WAIT`,
select an actionable Game Over direction and keep the runtime resumed. The
No Strategy terminal policy uses Home regardless of Retry so the following
post-run sequence occurs before any new battle:

1. Require verified Home `NEW_BATTLE`.
2. Open Workshop and inspect Shockwave Size, Bounce Shot Targets, and Bounce
   Shot Range lock details with read-only `enforce=False`; no checkbox is
   changed. The Workshop preset is recorded from the same pass.
3. Return Home and open Cards. The runtime expands the Home menu, independently
   verifies the Perks menu item, and opens Perks configuration itself. Normal
   Home/start handling remains held throughout.
4. The runtime selects and captures First Perk, Ban Perks, and Auto Pick tabs,
   scrolls each complete list, OCRs selected rows in display/priority order,
   closes the panel, and revalidates Home `NEW_BATTLE`.
5. The original `logs/battles/Battle*.json` and `.md` are updated atomically;
   Perks page evidence is retained under
   `logs/battle_observations/<battle-id>/perk_configuration/`. Only then is the
   next-battle path released.

Every observation records source, in-battle or post-run phase, confidence, and
timestamp. A complete but uncertain Perks capture is kept as raw page evidence
and reported as pending interpretation rather than accepted as a setting. A
Pause blocks all post-run input. A resumed pass continues from Perks when safe
or restores verified Home and repeats the current read-only stage. A failed
step leaves the next battle held with a logged reason and a bounded retry
instead of skipping the evidence boundary.

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
- The sole validation-only exception is a profile-declared exclusive
  validation of one ordinary `NEW_BATTLE`. Its durable receipt must be claimed
  atomically before the verified Home tap and must still name the same live
  runtime and ADB target before menu, Exit Battle, Surrender, and Game Over
  Home actions. Fresh battle evidence must exclude Tournament identity. A
  process restart, owner mismatch, `RESUME_BATTLE`, Tournament identity, or
  ambiguous transition fails closed and cannot inherit cleanup authority.
- A profile-declared setting may be repaired in battle only through an explicit
  safe-transition contract. Poison Swamp Stun `on` → `off` requires a freshly
  detected Poison Swamp tile, authoritative detail and checked-control matches,
  verification of `off`, and a verified return to `RUNNING/UW_MENU`. Damage
  Slider enforcement requires `RUNNING/ATTACK_MENU`, authoritative panel/mode/
  OCR evidence before adjustment. When both current and target values are exact
  powers of ten, that evidence may authorize only the bounded same-direction
  exponent-gap batch; runtime then reacquires settled OCR evidence, recomputes
  any remaining gap, requires strict progress and a verified final value, and
  returns to `RUNNING/ATTACK_MENU`. Unknown value sequences retain single-step
  feedback. Neither path authorizes Surrender or Home traversal.
- Orb Distance enforcement requires a verified Attack Range equal to the
  resolved preset's Range basis before opening the in-run Distance Adjuster.
  Both displayed values require authoritative OCR. Every arrow is freshly
  matched for one tap, followed by settled OCR that must move the selected row
  strictly closer to its target. Unknown, unchanged, cycling, non-progressing,
  or wrong-Range evidence fails closed, and success requires a verified return
  to the running side menu.
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
- No Strategy post-run Perks pages:
  `logs/battle_observations/<battle-id>/perk_configuration/`
- Tournament records: `logs/tournaments/Tournament*.json` and `Tournament*.md`
- Terminal Stats Tier is retained as observed evidence independently of the
  configured strategy. It can identify the Tier of an unconfigured Game Over,
  but it cannot by itself distinguish Farm from a manual or Milestone run. The
  localized purple sword badge can independently identify Attack Dissonance.
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
