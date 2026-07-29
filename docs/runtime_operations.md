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
`.codex/config.toml` selects a workspace permission profile that explicitly
allows `localhost` and `127.0.0.1`, so a session that loads the project layer
can reach the established host ADB server. Start a new Codex session after
changing that configuration so the project layer is reloaded.

Choose the execution path from the permissions declared for the current
session:

- When command network access is enabled, run one bounded ADB check through
  the normal workspace sandbox.
- When the session explicitly declares command network restricted, skip the
  isolated ADB probe and run the check once through approved host execution.
- When command network capability is not stated, try one bounded sandbox
  command. On an environment-level failure such as `could not install
  *smartsocket* listener: Operation not permitted`, retry immediately through
  approved host execution.

Do not preflight ADB with an extra known-failing invocation, and do not pause
the workflow merely to narrate the fallback. An isolated failure describes the
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
- An active lock record names its owner PID and target. A clean release rewrites
  the record as `state: released`, clears `pid`, and records `released_at`;
  that record is historical and needs no stale-process confirmation. A crash
  can leave `state: held` metadata after the OS lock has been released, so
  confirm a remaining PID and OS-lock evidence before treating it as a live
  owner.
- `actions.log` records what the automation intended, observed, and dispatched.
  Guarded and multi-step workflows follow the
  [action-log contract](#action-log-contract). The complete log retains both
  concise operator entries and paired diagnostic evidence.
- A fresh screenshot is authoritative for the visible UI and can disprove a
  stale wave/status hint.
- Never infer `RUNNING`, `PAUSED`, Game Over, or Home solely from a dated
  handoff.

## Action-log contract

The complete `actions.log` is the durable chronological record. Log levels
describe the role of an entry so a display can show a concise operational
narrative without discarding input or diagnostic evidence.

| Level | Contract |
| --- | --- |
| `ACTION` | One human-readable What/Why notice before an operator-meaningful workflow sends its first input. Nested implementation steps are not separate actions unless they are independently meaningful. |
| `RESULT` | One terminal outcome for each `ACTION`, with a disposition such as completed, no-op, deferred, interrupted, or failed and the most useful counts or observed values. A warning or error may supplement but does not replace the result. |
| `INPUT` | One individual device input such as a tap, swipe, or press. Pair coordinates, verification, dispatch mode, and retry mechanics at `DEBUG`. |
| `STATUS` | A periodic current-state snapshot. Status is retained in the log but belongs in a dedicated current-status presentation rather than the operational activity narrative. |
| `WARN` | An unexpected, persistent degradation with operator-relevant impact while automation can continue. Emit on the transition into degradation, rate-limit reminders, and record recovery. Expected negative searches and transient failures within their retry budget are not warnings. |
| `ERROR` / `FAIL` | A requested operation or runtime boundary could not complete safely. Preserve the existing distinction between a contained component error and a broader runtime failure. |
| `INFO` | General lifecycle or narrative detail that remains available outside the concise operational view. |
| `DEBUG` / `MATCH` / `STATE` | Internal decisions, coordinates, retries, detector evidence, and raw state transitions. |

The default Operational activity levels are `ACTION`, `RESULT`, `WARN`,
`ERROR`, and `FAIL`. Diagnostics includes `INPUT`, `DEBUG`, `MATCH`, and
`STATE`; `All levels` preserves the complete ordering. The GUI presents the
latest status and prior meaningful state transition separately while retaining
complete status history in `Status only` and `All levels`. Centralized runtime
input emitters record taps, swipes, and presses as `INPUT`.

The native GUI's default `Current run` activity scope is anchored by the
atomic `logs/activity_scope.json` ledger rather than inferred from status text.
Automation startup creates the ledger only when no valid scope exists, so
stopping and restarting the process during a battle reattaches to the same
activity view. Verified Home `NEW_BATTLE` evidence deliberately replaces the
scope when the next preflight begins, keeping that setup and the battle it
launches together. `Clear view` is a client-side cursor only: it does not edit
`actions.log`, and a new scope or log rotation resets it.

Low-level helpers should return structured reasons and keep ordinary outcomes
diagnostic. The workflow owner decides whether a result is a no-op, a failed
operation, or a persistent degradation worth surfacing to the operator.
Scrolling outcomes and ordinary OCR repair remain diagnostic. Repeated
game-speed verification and ADB connection failures become warnings only after
three consecutive failures, reminders are limited to once every five minutes,
and recovery is recorded.

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
specific failed requirement, the expected value, and its allowed choices, but
only after three complete setup attempts have failed. Each ordinary failed
attempt returns through the guarded Home cleanup and starts again from a fresh
Home capture. Pause/control interruption and an unsupported requirement do not
loop. After the third failed attempt, the runtime remains blocked until an
operator retries the check or explicitly waives that one requirement for the
current run. The native Windows app and browser fallback open a decision dialog
automatically. Closing it with **Decide later** leaves the gate pending and
performs no action.

Home setup retains the exact frame that authoritatively passed each Cards,
Workshop, Bots, and Guardians check. Its final combined configuration evidence
must agree with those individual checks before a battle may start. A
contradiction is a failed setup attempt; it cannot be retained as completed
evidence and later authorize an in-battle repair.

When an active Farm session preflight authoritatively appears to require a
supported Home-only repair, the runtime retries the same read-only validation
after its 30-second cooldown. Farm profiles require three consecutive
mismatches with the same failed-check set; success clears the count, and a
different set restarts it. Only an exhausted series may invoke the
profile-owned Surrender recovery under the ownership rules in `AGENTS.md`.
That repair transition is not a completed battle: at Game Over the runtime
skips Perks/More Stats capture and battle-record persistence, then follows the
guarded return-to-Home path. Natural Game Over and operator-owned battles
retain their ordinary capture policy.

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
data. The current Farm baseline bans **Lifesteal / Knockback Trade-Off**,
**Enemies Damage / Tower Damage Trade-Off**, **Defense Absolute**,
**Interest**, **Land Mine Damage**, and **Cash Bonus**. Its Auto Pick priority
is:

1. Perk Wave Requirement
2. Game Speed
3. Coin Trade-Off
4. Golden Tower Bonus
5. Black Hole Duration
6. Death Wave Quantity
7. Coins Bonus
8. Free Upgrade Chance
9. Orbs
10. Damage
11. Enemy Health / Tower Regen and Lifesteal Trade-Off
12. Boss Health / Boss Speed Trade-Off
13. Enemy Speed / Enemy Damage Trade-Off
14. Ranged Distance / Ranged Damage Trade-Off
15. Tower Damage / Boss Health Trade-Off
16. Chain Lightning Damage

The operator's planned continuation when additional ranking slots are
available is Inner Land Mines, Spotlight Damage, Bounce Shot, Defense Percent,
Health Regen, Max Health, Smart Missiles, then the Health Regen / Max Health
tradeoff. These rows are recognized semantically, but they are not required by
the current 16-slot profile.

Farm and Tournament also declare the recharge activation mode for both
death-prevention Cards:

- **Demon Mode: auto** — after its 300-wave recharge, the checked detail option
  allows Demon Mode to activate automatically.
- **Nuke: manual** — after its 300-wave recharge, the unchecked detail option
  makes Nuke available but does not activate it automatically.

At verified Home `NEW_BATTLE`, setup checks these settings from the Cards
inventory after selecting the strategy's deck. It searches for each exact card,
opens its detail with a template-verified long press, requires the matching
detail title and an unambiguous checkbox, and leaves an already-correct setting
untouched. A mismatch receives one target-verified checkbox tap followed by
fresh state verification. Missing cards, the wrong detail, or an ambiguous
checkbox blocks the startup gate. The card detail observed on 2026-07-25
described a 300-wave recharge for both Cards; only the activation checkbox is
strategy-configurable. The in-battle greyed-out Intro Sprint icon is not used
as Home preflight authority.

At verified Home `NEW_BATTLE`, setup opens the independently verified Perks
configuration control after returning from Cards. It reads the complete
selected Ban block and the strategy-sized Auto Pick prefix. A mismatch is
repaired before moving to the next tab. Extra bans are removed directly from
the fixed Selected Perks block; only missing required bans require an
Available-list checkbox search. Auto Pick uses the matched up arrow. Every
input first recaptures the panel and uniquely reacquires the same semantic row
at its current settled coordinates. After each Auto Pick up-arrow tap, setup
scrolls from the top, rebuilds the semantic rank, and requires exactly
one-rank upward progress before another tap. Completion requires an exact final
list followed by closing Perks and revalidating Home `NEW_BATTLE`. The complete
Home setup synchronizes persistent control before every tap and swipe. Pause
waits without cleanup input; after Resume it restores verified Home and
restarts setup with fresh evidence. A strategy that does not declare both
lists cannot trigger these changes. Uncertain OCR, an unavailable row,
unchanged input, or an exhausted move/scroll bound fails closed.

Module replacement treats each equipped slot's level as persistent slot-owned
state. Replacing an occupied Primary or Assist must present and accept the
verified level-transfer prompt. Moving two configured modules between Primary
and Assist uses a verified level-1 module of the same family as an
intermediate: the outgoing slot level is transferred to the intermediate, the
other configured module is transferred into its destination, and the displaced
configured module is transferred back over the intermediate. Do not resolve a
role cycle by Unequipping a configured module. Inventory selection requires an
aligned icon match with the configured confidence and runner-up margin,
followed by exact detail name, action, and level evidence. Missing or
unexpected transfer prompts, uncertain candidates, and unsettled overview or
filter rows fail closed.

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
Auto Perks unchanged, enforces Damage Slider `100%`, applies the matching
configured Orb Distance pair when Attack Range is `30.00m` or `98.38m`,
preserves other readable experimental Ranges, and verifies the configured
Ultimate Weapons and Spotlight Missiles. Staged
one-run waivers associated with the exact Tournament strategy request are
claimed and passed through the same Home setup as an ordinary startup; for
example, a configured Modules skip suppresses module inspection and changes
during validation. An unwaived Home preflight failure consumes the request and
reports its failed check without starting a battle. A conclusive in-battle pass
or failure starts guarded cleanup: only the same runtime/ADB owner may
Surrender that battle and return from Game Over to verified Home **New
Battle**. The status panel then reports either readiness and a
Tournament-launch prompt or the validation failure reason.

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
Slider `100%`, the Orb Distance preset selected by a configured observed Range,
and Poison Swamp Stun `on`, and reports other bad settings without acquiring
Home-repair authority. An unconfigured readable Range remains untouched.
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

If attached Tournament preflight finds an authoritative mismatch in a setting
that the pass only observes, it logs and retains that evidence, completes the
one-shot observer pass, and continues without publishing a gate decision.
Mismatched Modules, Workshop and Bot presets, Guardians, Cards, and retained
Home-only lock evidence cannot trigger a repair, block natural Tournament
Results/Game Over capture, or restart the inventory pass. Operators may still
Pause explicitly through the normal controls when manual intervention is
possible and desired.

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

1. Select `farm_t18`, `farm_t19`, `tournament`, or `none`.
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
- Orb Distance enforcement first requires authoritative Attack Range OCR. A
  dim Max-state value receives one adaptive-contrast OCR retry. Generated plans
  carry every configured Range preset: a recognized Range selects its matching
  Extra/Workshop pair even when the profile's nominal preset differs. A
  readable Range outside that set is recorded as an operator experiment and
  passes without opening or changing Distance Adjuster; genuinely unreadable
  Range evidence still fails closed. For a recognized Range, both displayed
  values require authoritative OCR. Every arrow is freshly matched for one
  tap, followed by settled OCR that must move the selected row strictly closer
  to its target. Because Distance Adjuster pauses combat and disables its
  controls while a Boss is present, an unavailable arrow or unchanged value
  closes the panel, waits for the active wave to advance with combat running,
  and performs a bounded retry from fresh panel evidence. The wait and every
  new panel session recheck runtime action authority. Unknown, cycling,
  non-progressing, or exhausted retry evidence fails closed, and success
  requires a verified return to the running side menu.
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
- Discarded completed records pending permanent deletion:
  `logs/discarded_battles/<discarded-at>__<battle-id>/`
- Canonical regression fixtures: `test/fixtures/`
- Actionable backlog: `PENDING_DEVELOPMENT.md`
- Open anomalies: `docs/observed_issues.md`
- Resolved recurrence history: `docs/issues/`

`logs/` and `screenshots/` are ignored runtime evidence, not substitutes for a
tracked issue entry. When an anomaly matters beyond the current thread, record
its date, symptom, evidence, status, and fix/test linkage in the issue ledger.

## Storage retention and completed-record discard

The Completed Battles window exposes **Discard selected...** only for an exact
selected Battle or Tournament id. After confirmation, the authenticated
control API moves the record's JSON and Markdown files into one quarantine
package under `logs/discarded_battles/`. Its `discard.json` records the source
directory, discard time, and permanent-purge deadline. The default deadline is
30 days; set the control-server
`--discarded-battle-retention-days` option to change it. A six-hour server
maintenance loop performs permanent expiry even when no Battle History client
is open, and normal history reads also sweep expired packages.

Before the deadline, manually restore a record by moving its JSON and Markdown
files from the quarantine package back to the `logs/battles/` or
`logs/tournaments/` source directory recorded in `discard.json`. Verify that
the record appears in Battle History before removing the leftover quarantine
metadata. Malformed or partial quarantine packages fail closed and are never
automatically purged.

The automation runtime separately sweeps these generated evidence trees at
startup and every six hours:

- `screenshots/matches/`
- `logs/battle_observations/`
- repository-local wave/coin sample directories explicitly configured on the
  runtime command line

Each tree defaults to a 30-day age limit and a 1 GiB size limit. Age expiry runs
first; if the remaining tree is still oversized, the oldest files more than
five minutes old are removed until it is bounded. The sweep never follows
symlinked subtrees and does not include `logs/battles/`, `logs/tournaments/`,
other screenshot directories, or `test/fixtures/`. Override the defaults with
positive integer environment values:

- `THETOWER_ARTIFACT_RETENTION_DAYS`
- `THETOWER_ARTIFACT_MAX_BYTES`
- `THETOWER_RETENTION_SWEEP_INTERVAL_SECONDS`

Development evidence that must outlive these limits is listed in
`config/protected_artifacts.txt`. Entries are repository-relative POSIX paths;
a trailing `/` protects a directory tree, and `*`, `?`, and `[]` may protect a
narrow file family. Protected files are never selected by either the age or
size pass, though their bytes still count toward the tree's size. The runtime
loads the manifest before touching any retention root and skips the complete
sweep if the file is absent, unreadable, or contains an absolute or
parent-traversing path.

Before relying on generated evidence in durable development documentation,
either promote it to a canonical regression fixture outside the cleanup
boundary or add its exact path, narrow family, or observation directory to the
protected manifest. Do not use a broad retention-root entry as a substitute
for selecting durable evidence.

`logs/actions.log` and an optional mission log rotate independently at 16 MiB
with five numbered backups by default. Use `TOWER_ACTION_LOG_MAX_BYTES` and
`TOWER_ACTION_LOG_BACKUP_COUNT` to change those integer limits. Rotation keeps
one operator-summary/diagnostic-detail group together.
