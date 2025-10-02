tools/gesture_logger.py
tools.gesture_logger.ScrcpyBridge — Class: manages the scrcpy worker process that streams JSON gestures from scrcpy_adb_input_bridge.py.
tools.gesture_logger.ScrcpyBridge.start() — R: None; S: [log]; E: OSError if process spawn fails.
tools.gesture_logger.ScrcpyBridge.ensure_running() — R: None; S: [log]; E: same as start() if restart needed and spawn fails.
tools.gesture_logger.ScrcpyBridge.stop() — R: None; S: [log]; E: None (kills after timeout if needed).
tools.gesture_logger.ScrcpyBridge.__enter__() — R: self (bridge ready after a short settle); S: [log]; E: propagate from ensure_running().
tools.gesture_logger.ScrcpyBridge.__exit__(exc_type, exc, tb) — R: None; S: [log]; E: None (best-effort stop).
tools.gesture_logger.ScrcpyBridge.flush_old() — R: None (discards buffered JSON gesture lines); S: [log]; E: None (no-op if stdout unavailable).
tools.gesture_logger.ScrcpyBridge.read_gesture() — R: dict describing one gesture (e.g., {"type":"tap","x":...} or {"type":"swipe","x1":...,"y1":...,"x2":...,"y2":...,"duration_ms":...}); S: [log]; E: RuntimeError if bridge not running/stdout unavailable or if process exits before a gesture; JSON decode errors are logged and skipped.
tools.gesture_logger.replay_gesture(gesture) — R: action result (injects the gesture on device); S: [adb][log]; E: CalledProcessError when ADB command fails (via adb_shell).
tools.gesture_logger.ensure_entry(dot_path) — R: (clickmap_dict, entry_dict) if created or found; (None, None) if user declines; S: [fs][log]; E: None (interactive prompt).
tools.gesture_logger.record_and_save(bridge, dot_path) — R: None; S: [fs][adb][log]; E: Propagates RuntimeError from read_gesture(); unsupported gesture types are logged and skipped.
tools.gesture_logger.main() — R: action result (interactive loop unless --name is provided); S: [loop][fs][adb][log]; E: KeyboardInterrupt cleanly exits; RuntimeError from read_gesture() propagates if not in the Ctrl+C path; CLI: --name <dot_path> saves exactly one gesture then exits.
