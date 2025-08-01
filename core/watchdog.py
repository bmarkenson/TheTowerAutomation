import re
import time
from core.automation_state import AUTOMATION
from core.adb_utils import adb_shell
from utils.logger import log

GAME_PACKAGE = "com.TechTreeGames.TheTower"

_last_foreground_pkg = None

def is_game_foregrounded():
    global _last_foreground_pkg
    result = adb_shell(["dumpsys", "activity", "activities"], capture_output=True)
    if result is None or result.returncode != 0:
        log("[WATCHDOG] Failed to query activity manager", level="WARN")
        return False

    output = result.stdout
    match = re.search(r"mCurrentFocus=Window\{.*?\s+(\S+)/(\S+)\}", output)
    if match:
        package = match.group(1)
        if package != _last_foreground_pkg:
            if _last_foreground_pkg is None:
                log(f"[WATCHDOG] Started — current foreground app: {package}", level="DEBUG")
            else:
                log(f"[WATCHDOG] Resumed app: {package}", level="DEBUG")
            _last_foreground_pkg = package
        return package.lower() == GAME_PACKAGE.lower()

    log("[WATCHDOG] Could not find mCurrentFocus", level="WARN")
    return False

def bring_to_foreground():
    adb_shell([
        "monkey", "-p", "com.TechTreeGames.TheTower",
        "-c", "android.intent.category.LAUNCHER", "1"
    ])
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)

def restart_game():
    log("[WATCHDOG] Restarting game via monkey intent", "INFO")

    # Launch the game
    adb_shell([
        "monkey", "-p", GAME_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1"
    ])
    time.sleep(5)

    # Set state to unknown — main loop will detect screen state
    from core.automation_state import AUTOMATION
    AUTOMATION.set_state("UNKNOWN")

    log("[WATCHDOG] Game launched — deferring to main loop for state detection", "INFO")

def watchdog_process_check(interval=30):
    while True:
        try:
            result = adb_shell(["pidof", GAME_PACKAGE], capture_output=True, check=False)

            if result is None:
                log("[WATCHDOG] ADB shell returned None for pidof", "ERROR")
                time.sleep(interval)
                continue

            pid_running = result.returncode == 0 and result.stdout.strip()
            foregrounded = is_game_foregrounded()

            if not pid_running:
                log("[WATCHDOG] Game process not running. Restarting.", "WARN")
                restart_game()
            elif not foregrounded:
                log("[WATCHDOG] Game is backgrounded. Bringing to foreground.", "WARN")
                bring_to_foreground()

        except Exception as e:
            log(f"[WATCHDOG ERROR] {e}", "ERROR")

        time.sleep(interval)


