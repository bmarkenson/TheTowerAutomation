$PROJECT_ROOT/core/tap_dispatcher.py — Library
core.tap_dispatcher.log_tap(x, y, label) — Returns: None; Side effects: [log]
core.tap_dispatcher.tap(x, y, label=None) — Returns: enqueues a device tap to be executed by the background worker thread; Side effects: [tap], [log]
core.tap_dispatcher.main() — Returns: None; Side effects: [loop], [log]; Notes: long-running dispatcher process; Ctrl+C to exit
