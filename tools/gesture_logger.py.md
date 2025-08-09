$PROJECT_ROOT/tools/gesture_logger.py — Entrypoint
tools.gesture_logger.ScrcpyBridge — Class: manages the scrcpy worker process that streams JSON gestures from scrcpy_adb_input_bridge.py.
tools.gesture_logger.ScrcpyBridge.start() — Returns: None; Side effects: [log]; Errors: OSError if process spawn fails.
tools.gesture_logger.ScrcpyBridge.ensure_running() — Returns: None; Side effects: [log]; Errors: same as start() if restart needed and spawn fails.
tools.gesture_logger.ScrcpyBridge.stop() — Returns: None; Side effects: [log]; Errors: None (kills after timeout if needed).
tools.gesture_logger.ScrcpyBridge.__enter__() — Returns: self (bridge ready after a short settle); Side effects: [log]; Errors: propagate from ensure_running().
tools.gesture_logger.ScrcpyBridge.__exit__(exc_type, exc, tb) — Returns: None; Side effects: [log]; Errors: None (best-effort stop).
tools.gesture_logger.ScrcpyBridge.flush_old() — Returns: None (discards buffered JSON gesture lines); Side effects: [log]; Errors: None (no-op if stdout unavailable).
tools.gesture_logger.ScrcpyBridge.read_gesture() — Returns: dict describing one gesture (e.g., {"type":"tap","x":...} or {"type":"swipe","x1":...,"y1":...,"x2":...,"y2":...,"duration_ms":...}); Side effects: [log]; Errors: RuntimeError if bridge not running/stdout unavailable or if process exits before a gesture; JSON decode errors are logged and skipped.
tools.gesture_logger.replay_gesture(gesture) — Returns: action result (injects the gesture on device); Side effects: [adb][log]; Errors: CalledProcessError when ADB command fails (via adb_shell).
tools.gesture_logger.ensure_entry(dot_path) — Returns: (clickmap_dict, entry_dict) if created or found; (None, None) if user declines; Side effects: [fs][log]; Errors: None (interactive prompt).
tools.gesture_logger.record_and_save(bridge, dot_path) — Returns: None; Side effects: [fs][adb][log]; Errors: Propagates RuntimeError from read_gesture(); unsupported gesture types are logged and skipped.
tools.gesture_logger.main() — Returns: action result (interactive loop unless --name is provided); Side effects: [loop][fs][adb][log]; Errors: KeyboardInterrupt cleanly exits; RuntimeError from read_gesture() propagates if not in the Ctrl+C path; CLI: --name <dot_path> saves exactly one gesture then exits.
