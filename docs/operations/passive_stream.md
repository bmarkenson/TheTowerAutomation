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
  --max-fps=15 --video-bit-rate=2M --time-limit=60 \
  --window-title=thetower-passive-observation
```

Adjust `PORT` only from the inspected target and choose the shortest useful
time limit. Run a GUI viewer through the approved host path when the sandbox
cannot reach the display or host ADB server. Do not run `adb connect`, start or
kill the ADB server, or omit the exact serial as a workaround.

`tools/scrcpy_adb_input_bridge.py` is not a passive viewer. Although it starts
scrcpy with `--no-control`, it installs a mouse listener that translates clicks
into ADB taps, swipes, Back, and Home. Its use requires the separate input
workflow and authority.

## Coexistence check and stop conditions

During the first bounded use of a transport, and whenever current evidence
suggests contention, confirm that production's `screenshots/latest.json`
continues to advance and that control-surface ADB status remains connected with
no active warning. Stop the viewer immediately when any of these occurs:

- the runtime or exact target changes;
- production capture fails or stops advancing across two expected capture
  cycles;
- ADB reports an outage, warning, or repeated capture failure;
- the viewer exposes or accepts a control channel; or
- the operator asks it to stop.

A transport-specific failure does not prohibit every streaming implementation.
Record which transport and options failed. In particular, Android
`screenrecord` contention is not automatically attributed to `scrcpy`.

## Cleanup and report

Let the finite deadline close the viewer or terminate its exact task-owned
process, then verify that it exited. Recheck control-surface ADB health and
confirm a newer complete production frame on the unchanged target. Report the
transport, options, duration, whether production frames advanced, any capture
or connection errors, and cleanup outcome. Do not turn volatile wave, screen,
PID, or target facts into durable guidance.
