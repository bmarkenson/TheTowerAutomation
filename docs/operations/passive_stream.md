# Bounded Passive Stream

Use this procedure when an operator or worker needs a continuously updating
view of the current device. It authorizes observation only. Input, navigation,
Pause, process replacement, ADB connection management, and battle-boundary
actions remain under their existing owners.

## Authority and preflight

An explicit operator instruction in the current task is sufficient to
authorize one on-demand passive stream. Complete
[`../live_preflight.md`](../live_preflight.md) first and resolve the exact ADB
target from matching control, runtime, held-lock, and connection evidence. An
interactive development lease is not required because the viewer sends no
input.

Before launch, verify that production is not in a capture-sensitive validation
or transition whose result would be obscured by extra encoder or ADB load. An
ordinary running battle may remain under automation. Record the latest
production-frame time and confirm that the configured target is `device`.

## Viewer boundary

Start at most one task-owned viewer with all of these properties:

- the exact inspected target is supplied explicitly;
- device control and audio are disabled;
- frame rate and bit rate are bounded to what the observation needs;
- both the device-side stream and host process have a finite deadline; and
- the launching session retains the process through cleanup.

For the installed `scrcpy` viewer, a representative 60-second observation is:

```bash
timeout --signal=TERM --kill-after=5s 70s \
  scrcpy --serial=localhost:PORT --no-control --no-audio \
  --max-size=1280 --max-fps=15 --video-bit-rate=2M --time-limit=60 \
  --window-title=thetower-passive-observation
```

Adjust `PORT` only from the inspected target and choose the shortest useful
time limit. Run a GUI viewer through the approved host path when the sandbox
cannot reach the display or host ADB server. Do not run `adb connect`, start or
kill the ADB server, or omit the exact serial as a workaround.

The frame-rate limit is a load boundary: motion in a 15 FPS viewer will look
less smooth even when the emulator and production capture are unaffected. If
the emulator itself slows, prefer a shorter duration, lower maximum size, frame
rate, or bit rate before increasing stream load. Use `--print-fps` when a
bounded comparison needs to distinguish viewer cadence from host or emulator
degradation.

`tools/scrcpy_adb_input_bridge.py` is not a passive viewer. Although it starts
scrcpy with `--no-control`, it installs a mouse listener that translates clicks
into ADB taps, swipes, Back, and Home. Its use requires the separate input
workflow and authority.

## Optional game-speed preparation

Passive-stream authority alone does not authorize changing game speed. When
the operator also requests a slower observation, use the existing guarded
control-surface procedure. Use x2 as the normal temporary ceiling when the
observed battle speed is higher than x2, and x1 for a transition that is
otherwise too fast to interpret. Never raise an already slower battle to x2 as
stream preparation. A slower game makes motion easier to follow at a bounded
stream frame rate and may leave more render headroom, but it is not evidence
that encoder, renderer, or ADB contention has been fixed.

Record the prior persistent target and its request identity before changing
it. Require normal acknowledgement and fresh visible confirmation; a failed
enforcement does not authorize a direct tap. After the stream, restore the
prior target only if the temporary request is still the current worker-owned
intent and no operator or runtime boundary has superseded it. Follow the
[process-control procedure](process_control.md) for the mutation and cleanup.

## Coexistence check and stop conditions

During the first bounded use of a transport, and whenever current evidence
suggests contention, confirm that production's `screenshots/latest.json`
continues to advance and that control-surface ADB status remains connected with
no active warning. Stop the viewer immediately when any of these occurs:

- the runtime or exact target changes;
- production capture fails or stops advancing across two expected capture
  cycles;
- ADB reports an outage, warning, or repeated capture failure;
- host or emulator degradation becomes material to the running battle;
- the viewer exposes or accepts a control channel; or
- the operator asks it to stop.

A transport-specific failure does not prohibit every streaming implementation.
Record which transport and options failed. In particular, Android
`screenrecord` contention is not automatically attributed to `scrcpy`.
An operator may accept a moderate renderer cost for a short observation; that
does not turn the viewer into input. Keep the duration bounded, use the
lowest-load useful profile, and report the measured or directly observed cost.

## Cleanup and report

Let the finite deadline close the viewer or terminate its exact task-owned
process, then verify that it exited. Recheck control-surface ADB health and
confirm a newer complete production frame on the unchanged target. Report the
transport, options, duration, whether production frames advanced, any capture
or connection errors, any observed viewer or emulator frame-rate degradation,
and cleanup outcome. Do not turn volatile wave, screen, PID, or target facts
into durable guidance.
