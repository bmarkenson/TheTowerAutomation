
core/tap_dispatcher.py
core.tap_dispatcher.log_tap(x, y, label) — R: None; S: [log]
core.tap_dispatcher.tap(x, y, label=None) — R: enqueues a device tap to be executed by the background worker thread; S: [tap], [log]
core.tap_dispatcher.main() — R: None; S: [loop], [log]; Notes: long-running dispatcher process; Ctrl+C to exit
