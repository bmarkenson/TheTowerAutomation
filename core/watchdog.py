import re
import time
from core.automation_state import AUTOMATION, RunState
from core.adb_utils import adb_shell
from utils.logger import log

GAME_PACKAGE = "com.TechTreeGames.TheTower"

_last_foreground_pkg = None

def _parse_pkg_from_text(text: str):
    """
    Try several known patterns across Android versions/ROMs/emulators.
    Returns package str or None.
    """
    if not text:
        return None

    # Pattern 1: window mCurrentFocus (common on emu & older devices)
    m = re.search(r"mCurrentFocus=Window\{.*?\s+(\S+)/\S+\}", text)
    if m:
        return m.group(1)

    # Pattern 2: topResumedActivity (newer AOSP)
    m = re.search(r"topResumedActivity.*?\s+(\S+)/\S+", text)
    if m:
        return m.group(1)

    # Pattern 3: mResumedActivity (older/newer mixes)
    m = re.search(r"mResumedActivity.*?\s+(\S+)/\S+", text)
    if m:
        return m.group(1)

    # Pattern 4: focused app (very old fallbacks)
    m = re.search(r"mFocusedApp=.*\s+(\S+)/\S+", text)
    if m:
        return m.group(1)

    return None

def _get_foreground_package():
    """
    Returns current foreground package or None if not determinable.
    Tries multiple dumpsys surfaces for emulator/BlueStacks robustness.
    """
    # First try window service (often most reliable under emu)
    res = adb_shell(["dumpsys", "window", "windows"], capture_output=True, check=False)
    if res and res.returncode == 0:
        pkg = _parse_pkg_from_text(res.stdout)
        if pkg:
            return pkg

    # Fallback to activity service (formats vary by release)
    res = adb_shell(["dumpsys", "activity", "activities"], capture_output=True, check=False)
    if res and res.returncode == 0:
        pkg = _parse_pkg_from_text(res.stdout)
        if pkg:
            return pkg

    return None

def is_game_foregrounded():
    """
    Returns True if GAME_PACKAGE is currently the foreground app, else False.
    Logs foreground changes for observability.
    """
    global _last_foreground_pkg
    package = _get_foreground_package()
    if package:
        if package != _last_foreground_pkg:
            if _last_foreground_pkg is None:
                log(f"[WATCHDOG] Started — current foreground app: {package}", level="DEBUG")
            else:
                log(f"[WATCHDOG] Foreground changed: {package}", level="DEBUG")
            _last_foreground_pkg = package
        return package.lower() == GAME_PACKAGE.lower()
    else:
        log("[WATCHDOG] Could not determine foreground app", level="WARN")
        return False

def bring_to_foreground():
    """
    Attempts to bring GAME_PACKAGE to foreground via monkey intent.
    """
    adb_shell([
        "monkey", "-p", GAME_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1"
    ], check=False)
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)

def restart_game():
    """
    Force-stops GAME_PACKAGE and relaunches via monkey; sets automation state to UNKNOWN.
    """
    log("[WATCHDOG] Restarting game via monkey intent", "INFO")

    # Hard-stop first to avoid stale process/session on emulators
    adb_shell(["am", "force-stop", GAME_PACKAGE], check=False)

    # Launch the game (monkey keeps us activity-agnostic)
    adb_shell([
        "monkey", "-p", GAME_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1"
    ], check=False)
    time.sleep(6)

    # Set state to unknown — main loop will detect screen state
    from core.automation_state import AUTOMATION, RunState
    AUTOMATION.state = RunState.UNKNOWN

    log("[WATCHDOG] Game launched — deferring to main loop for state detection", "INFO")

def _pid_running(package: str) -> bool:
    """
    Emulator-safe process existence check.
    Tries pidof, then falls back to ps -A matching.
    """
    res = adb_shell(["pidof", package], capture_output=True, check=False)
    if res and res.returncode == 0 and res.stdout.strip():
        return True

    # Fallback: ps scan (avoid false positives by splitting columns)
    res = adb_shell(["ps", "-A"], capture_output=True, check=False)
    if not res or res.returncode != 0 or not res.stdout:
        return False
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts and parts[-1] == package:
            return True
    return False

def watchdog_process_check(interval=30):
    """
    Supervisory loop: ensures process is running and foregrounded; restarts or foregrounds as needed.
    """
    while True:
        try:
            pid_running = _pid_running(GAME_PACKAGE)
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
