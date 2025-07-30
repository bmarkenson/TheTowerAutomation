import time
from core.input_named import tap_named, load_clickmap
from core.automation_state import AUTOMATION
from core.adb_utils import adb_shell
from utils.logger import log
from core.ss_capture import capture_adb_screenshot
from core.tap_dispatcher import tap
from matchers.resume_screen import detect_resume_button

GAME_PACKAGE = "com.TechTreeGames.TheTower"

def is_game_foregrounded():
    result = adb_shell(["dumpsys", "window", "windows"], capture_output=True)
    if result is None or result.returncode != 0:
        return False
    output = result.stdout.lower()
    return "com.techtreegames.thetower" in output and "mcurrentfocus" in output

def bring_to_foreground():
    adb_shell([
        "monkey", "-p", "com.TechTreeGames.TheTower",
        "-c", "android.intent.category.LAUNCHER", "1"
    ])
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)

def restart_game():
    adb_shell(["monkey", "-p", GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"])
    log("[WATCHDOG] Sent monkey intent to start the game.", "INFO")

    clickmap = load_clickmap()

    for attempt in range(15):
        time.sleep(2)
        screen = capture_adb_screenshot()
        if screen is None:
            continue

        matched_name, confidence = detect_resume_button(screen)
        if matched_name:
            log(f"[WATCHDOG] Detected {matched_name} (conf {confidence:.2f})", "INFO")
            tap_named(clickmap, matched_name)
            break
        else:
            log("[WATCHDOG] No resume button match on this frame.", "DEBUG")
        if matched_name:
            log(f"[WATCHDOG] Detected {matched_name} (conf {confidence:.2f})", "INFO")
            tap_named(clickmap, matched_name)
            break
    else:
        log("[WATCHDOG] Resume button not found after restart. Manual intervention may be needed.", "WARN")
    time.sleep(5)

def watchdog_process_check(interval=30):
    while True:
        if AUTOMATION.get_state() != "RUNNING":
            time.sleep(interval)
            continue

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


