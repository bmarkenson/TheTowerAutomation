automation/run_demon_mode.py
automation.run_demon_mode.main(delay=2, once=False) — R: action result (mission loop calling handlers.mission_demon_mode.run_demon_mode(); exits after one iteration when once=True); S: [loop][log][adb]; E: KeyboardInterrupt stops loop; unhandled exceptions are logged and the loop retries after delay.
