# Managed Runtime Operations

Complete [`live_preflight.md`](../live_preflight.md) before changing a live
service, target, Strategy, or attachment. Linux unit installation and upgrade
belong to [`deploy/systemd/README.md`](../../deploy/systemd/README.md); native
client operation belongs to the
[`Windows README`](../../windows/TheTower.ControlSurface/README.md).

## Attach to a current battle

Before managed Start, choose **Validate current battle if attached** or **Skip
checks for current battle**. Fresh active-battle or Home **Resume Battle**
evidence creates the attachment. A matching completed session receipt may be
reused only with unchanged Current-run identity and Strategy check fingerprint;
missing or unreadable continuity, a changed battle, or changed checks reruns or
defers them.

Attachment checks are read-only unless the profile explicitly declares a
guarded battle-only `run_when_attached` action. They never select Home presets,
equip loadouts, leave through Home, restart, or acquire Surrender authority.
Home-only evidence unavailable from a bound save remains explicitly deferred.
Attachment ends at Game Over, Tournament Results, or verified Home
`NEW_BATTLE`, where ordinary gates rearm.

## Reload automation for the current battle

For a checked-in Python update, prefer the guarded control-surface reload over
raw Stop/Start or `systemctl restart`:

1. Persist indefinite Pause and wait for the current runtime acknowledgement.
2. Require that same PID/lock owner to publish a fresh post-request `RUNNING`
   observation while actions remain blocked.
3. Stop the fixed automation unit; launch one replacement with
   `startup_gates=next_run`; immediately restore the configured future policy.
4. Require a distinct systemd `MainPID`, matching held target lock, attached-
   policy startup evidence, Pause consumption, and first fresh observation.
5. Restore the prior `RUNNING`, indefinite Pause, or unexpired timed Pause.

An initial precondition failure changes nothing. Any failure after Pause
preparation leaves control Paused. Reload does not fabricate completed gates;
attachment suppression ends at the next authoritative boundary, and process-
local samples restart as “since attachment.”

## Switch the live ADB target

1. Select indefinite Pause and wait for acknowledgement; timed Pause is not
   accepted.
2. Move the emulator, enter the new exact localhost port, and select Switch.
3. Require the new target lock, `device` transport, supported fresh frame, and
   runtime handoff acknowledgement before the old lock is released.
4. Verify the fresh screen and Resume only when appropriate.

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
