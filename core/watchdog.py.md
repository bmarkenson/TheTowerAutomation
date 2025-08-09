$PROJECT_ROOT/core/watchdog.py — Library
core.watchdog.is_game_foregrounded() — Returns: True iff GAME_PACKAGE is currently foregrounded; logs foreground changes. Side effects: [adb][log]
core.watchdog.bring_to_foreground() — Returns: action result (monkey launch intent sent, 5s wait). Side effects: [adb][log]
core.watchdog.restart_game() — Returns: action result (force-stop then monkey relaunch; sets AUTOMATION.state=UNKNOWN). Side effects: [adb][state][log]
core.watchdog.watchdog_process_check(interval=30) — Returns: [loop] supervisory check; restarts or foregrounds app as needed. Side effects: [adb][state][log]; Errors: KeyboardInterrupt stops loop; other exceptions logged and loop continues
