$PROJECT_ROOT/automation/run_demon_mode.py — Entrypoint
automation.run_demon_mode.main(delay=2, once=False) — Returns: action result (mission loop calling handlers.mission_demon_mode.run_demon_mode(); exits after one iteration when once=True); Side effects: [loop] [log] [adb]; Errors: KeyboardInterrupt stops loop; unhandled exceptions are logged and the loop retries after delay.
