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

**Start Battle** is available only from fresh, owner-matched Home **New
Battle** evidence. If requested while Paused it remains `awaiting_enable`;
explicit **Enable Automation** revalidates the same runtime, target, activity
scope, and boundary before normal new-run gates receive action authority.
Once the verified Home control is tapped, status reports `action_dispatched`;
the workflow continues to suppress unrelated input until lifecycle adoption or
a visible interrupted/failed result.

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

The revision-28 implementation deliberately remains input-blocked in
`validating_save`. Fresh-save identity/configuration validation and its
allowlisted unresolved-field UI fallback are being delivered by a separate
non-overlapping work slice. Until that integration advances the workflow to
`ready`, do not use this feature branch for a live attachment and do not treat
the pending status as attached. No automatic or skipped-check attachment path
is available.

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
