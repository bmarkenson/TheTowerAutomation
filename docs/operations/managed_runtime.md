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

**Enable Automation** changes action authority only. While the runtime is still
waiting for its initial battle intent, Enable at Home does not serialize a Home
baseline, run configuration setup, claim a Tournament validation battle, or
dispatch New Battle/Resume Battle. Those paths require the separate matching
battle intent. An explicitly requested setup capture remains a separate
workflow under its own exact evidence and serialization authority.

**Start Battle** is available only from fresh, owner-matched Home **New
Battle** evidence. If requested while Paused it remains `awaiting_enable`;
explicit **Enable Automation** revalidates the same runtime, target, activity
scope, and boundary before normal new-run gates receive action authority.
Once the verified Home control is tapped, status reports `action_dispatched`;
the workflow continues to suppress unrelated input until lifecycle adoption or
a visible interrupted/failed result.
If Pause, Stop, or Take Manual Control arrives during a Home configuration
route, setup yields at the first denied input and performs no cleanup action.
Observation and acknowledgement continue. A later Enable restores Home only
when the original workflow still owns the exact runtime, target, and activity
scope; otherwise that pending recovery is discarded.

Stopping interrupts unfinished battle and manual-control workflows. Repeating
an already satisfied Start or Stop is reported as a no-op. The old attached
reload and Start-time attachment-policy controls are retired: after a process
replacement, inspect fresh observation and issue a new exact battle intent.

## Attach to a current battle

**Attach to Battle** is available only from fresh active-battle or Home
**Resume Battle** evidence. It never starts a new battle as a fallback and does
not adopt the observed battle merely because the intent was accepted. The
runtime first revalidates the exact PID, ADB target/generation, activity scope,
and observed boundary.

The runtime remains input-blocked in `validating_save` while it attempts one
guarded exact-target serialization and restores the source. A usable save
binds active-round identity to the final activity scope. If the save is absent,
uses an unsupported revision, has an incompatible shape, or cannot be
projected after safe restoration, the runtime automatically uses guarded
Battle History instead and writes a target/scope-bound UI receipt. `ready`
means the existing battle is validated for observation-only attachment through
one of those routes; it does not select a Strategy. A changed owner, target,
scope, action authority, or unproved restoration still blocks input and leaves
Automation Paused.

To monitor and collect without changing the battle, leave the active Strategy
as **No Strategy** after attachment. To apply the selected Strategy, use the
separate **Switch this battle** action only after attachment completes. That
adoption does not authorize Surrender. If a Strategy later reports a problem
that can only be fixed between battles, automation may Surrender only when the
runtime offers **Surrender this battle and repair setup** and the operator
selects that exact one-shot option. It records the nonrepresentative result,
returns to verified Home, and lets ordinary Home repair and the separately
selected future-battle policy continue. The one-shot permission does not
select a later battle or grant Surrender authority beyond that exact repair.

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

**Return Control** refreshes passive observation while Pause remains in force.
It is available only at exact-bound Home New, Home Resume, active battle, or
Game Over evidence; Tournament Results, unknown state, or missing target/scope
binding is visibly unavailable. Explicit **Enable Automation** then authorizes
only reconciliation. The runtime first requests a newly forced save (or the
bound Game Over natural save). If it is usable, mapped evidence reconciles
battle identity and configuration; a trusted mismatch Pauses for operator
review. If the save is unusable after safe restoration, active and resumable
Return uses Battle History plus every supported active-Strategy UI verifier,
Home New uses every supported Home configuration verifier, and Game Over uses
the full Game Stats/Perks/More Stats collector. Each route writes a bound typed
reconciliation receipt before ordinary input returns. Do not use Enable as a
shortcut around Return.
If a Home New refresh loses its source restoration, owner, target, scope, or
authority binding after backgrounding, Return becomes failed/interrupted and
Automation remains Paused.
Do not retry by repeatedly selecting Enable; start a new explicit Return only
after reviewing the reported boundary.
If the forced save succeeds but the bounded Home UI repair exhausts instead,
Return reports `awaiting_manual_correction` with the failed check and exact
reason. Make the reported manual change while Pause remains acknowledged, then
select Enable once. That explicit retry discards the former process-local claim
and requests a new serialization before any UI validation; it never reuses the
old receipt or retries from a heartbeat.

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
reports `failed` and enters a Strategy Gate so observation and safe gem
collectors continue. A proved Home New contradiction, or an attempted
lifecycle transition whose source restoration cannot be proved, reports
`failed` and persists Automation Paused. Review the reported boundary before
requesting another capture.

There is one deliberate no-input path for manual changes: if Return Control
already forced an exact active-battle save and then Paused on a trusted mismatch,
**Capture current setup as…** may review that retained process-local acquisition.
The UI identifies it as Return Control evidence. It requests no second refresh,
does not use a durable receipt as replay authority, and saving the result does
not resolve Return Control or resume automation.

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
