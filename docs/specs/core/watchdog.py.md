
core/watchdog.py
core.watchdog.is_game_foregrounded() — R: True iff GAME_PACKAGE is currently foregrounded; logs foreground changes. S: [adb][log]
core.watchdog.bring_to_foreground() — R: action result (monkey launch intent sent, 5s wait). S: [adb][log]
core.watchdog.restart_game() — R: action result (force-stop then monkey relaunch; sets AUTOMATION.state=UNKNOWN). S: [adb][state][log]
core.watchdog.watchdog_process_check(interval=30) — R: [loop] supervisory check; restarts or foregrounds app as needed. S: [adb][state][log]; E: KeyboardInterrupt stops loop; other exceptions logged and loop continues
