# core/watchdog.py
"""
Foreground/process watchdog for the game app.

spec_legend:
  r: Return value (shape & invariants)
  s: Side effects (project tags like [adb][log][sleep][state][loop])
  e: Errors/exceptions behavior
  p: Parameter notes beyond the signature
  notes: Usage guidance / invariants

defaults:
  game_package: com.TechTreeGames.TheTower
  detection:
    - Foreground app inferred via dumpsys window/windows → activity/activities
    - Multiple textual patterns supported for broad Android/emu coverage
  targeting: Uses core.adb_utils.adb_shell; device selection follows adb_utils precedence
  logging: Foreground package changes and retry detail are INFO/DEBUG;
    persistent connectivity failures are rate-limited WARN
  sleep_delays:
    - bring_to_foreground: ~5s, restart_game: ~6s
  globals:
    - _last_foreground_pkg caches last seen foreground for change logging only
"""

import os
import re
import subprocess
import threading
import time
from typing import Callable

from core.run_state import AUTOMATION, RunState
from core.adb_utils import adb_shell, ADB_DEVICE_ID
from core.adb_target_session import ADB_TARGET_OPERATION_LOCK
from utils.logger import log

GAME_PACKAGE = "com.TechTreeGames.TheTower"
ADB_CONNECTION_WARNING_AFTER_FAILURES = 3
ADB_CONNECTION_WARNING_REPEAT_S = 5 * 60.0
"""
spec:
  name: GAME_PACKAGE
  kind: const
  r: Package name string used by monkey/force-stop checks.
  notes:
    - Override only if the target app id changes; other functions depend on it.
"""

_last_foreground_pkg = None
"""
spec:
  name: _last_foreground_pkg
  kind: module-global cache
  r: str|None (last detected foreground package), used to suppress noisy logs.
"""


class _AdbConnectionLogState:
    """Promote only persistent ADB connection failures to warnings."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        warning_after_failures: int = ADB_CONNECTION_WARNING_AFTER_FAILURES,
        warning_repeat_s: float = ADB_CONNECTION_WARNING_REPEAT_S,
    ) -> None:
        self._clock = clock
        self._warning_after_failures = max(1, int(warning_after_failures))
        self._warning_repeat_s = max(0.0, float(warning_repeat_s))
        self._lock = threading.Lock()
        self._target = ""
        self._failures = 0
        self._warning_active = False
        self._last_warning_at: float | None = None

    def record(self, target: str, *, connected: bool) -> None:
        now = self._clock()
        with self._lock:
            if target != self._target:
                self._target = target
                self._failures = 0
                self._warning_active = False
                self._last_warning_at = None

            if connected:
                failures = self._failures
                warning_was_active = self._warning_active
                self._failures = 0
                self._warning_active = False
                self._last_warning_at = None
                if failures:
                    log(
                        f"[WATCHDOG] ADB target {target} recovered after "
                        f"{failures} failed connection attempt(s)",
                        "INFO" if warning_was_active else "DEBUG",
                    )
                return

            self._failures += 1
            if self._failures < self._warning_after_failures:
                return
            warning_due = (
                not self._warning_active
                or self._last_warning_at is None
                or now - self._last_warning_at >= self._warning_repeat_s
            )
            self._warning_active = True
            if not warning_due:
                return
            qualifier = (
                "remains unavailable"
                if self._last_warning_at is not None
                else "is unavailable"
            )
            log(
                f"[WATCHDOG] ADB target {target} {qualifier} after "
                f"{self._failures} connection attempts; automation inputs "
                "remain suspended while retries continue",
                "WARN",
            )
            self._last_warning_at = now


_adb_connection_log_state = _AdbConnectionLogState()


def _parse_pkg_from_text(text: str):
    """
    spec:
      name: _parse_pkg_from_text
      signature: _parse_pkg_from_text(text:str) -> str|None
      r: Package name if any pattern matches; else None.
      s: none
      e: none (pure function)
      notes:
        - Supports multiple dumpsys formats (mCurrentFocus, topResumedActivity, mResumedActivity, mFocusedApp).
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
    spec:
      name: _get_foreground_package
      signature: _get_foreground_package() -> str|None
      r: The currently foregrounded package name, or None if undetermined.
      s: [adb]
      e:
        - Suppresses CalledProcessError by using check=False in adb_shell.
        - Returns None on any non-zero exit or unparsable output.
      notes:
        - Tries dumpsys window windows first, then dumpsys activity activities.
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
    spec:
      name: is_game_foregrounded
      signature: is_game_foregrounded() -> bool
      r: True if GAME_PACKAGE is foreground; False otherwise.
      s: [adb][log]
      e: none (logs WARN when foreground cannot be determined)
      notes:
        - Logs any change in the detected foreground package since the last call.
    """
    global _last_foreground_pkg
    package = _get_foreground_package()
    if package:
        if package != _last_foreground_pkg:
            if _last_foreground_pkg is None:
                log(
                    f"[WATCHDOG] Started — current foreground app: {package}",
                    level="DEBUG",
                    console=True,
                )
            else:
                log(f"[WATCHDOG] Foreground changed: {package}", level="DEBUG")
            _last_foreground_pkg = package
        return package.lower() == GAME_PACKAGE.lower()
    else:
        log("[WATCHDOG] Could not determine foreground app", level="WARN")
        return False


def bring_to_foreground():
    """
    spec:
      name: bring_to_foreground
      signature: bring_to_foreground() -> None
      r: null
      s: [adb][log][sleep]
      e: none (uses check=False; best-effort)
      notes:
        - Sends a single monkey LAUNCHER intent for GAME_PACKAGE and waits ~5s.
    """
    adb_shell([
        "monkey", "-p", GAME_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1"
    ], check=False)
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)


def restart_game():
    """
    spec:
      name: restart_game
      signature: restart_game() -> None
      r: null
      s: [adb][state][log][sleep]
      e: none (best-effort; uses check=False)
      notes:
        - Force-stops GAME_PACKAGE, relaunches via monkey, then sets AUTOMATION.state=UNKNOWN.
        - Sleeps ~6s after relaunch to allow surface creation.
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
    from core.run_state import AUTOMATION, RunState
    AUTOMATION.state = RunState.UNKNOWN

    log("[WATCHDOG] Game launched — deferring to main loop for state detection", "INFO")


def _pid_running(package: str) -> bool:
    """
    spec:
      name: _pid_running
      signature: _pid_running(package:str) -> bool
      r: True if a process with exact package name is running; else False.
      s: [adb]
      e: none (returns False on any adb failure)
      notes:
        - Uses pidof first; falls back to parsing `ps -A` and matching the final column exactly.
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


def _adb_target() -> str:
    return os.getenv("ADB_DEVICE") or ADB_DEVICE_ID or ""


def _adb_is_connected(target: str) -> bool:
    try:
        res = subprocess.run(["adb", "devices"], capture_output=True, text=True, check=False)
        if res.returncode != 0 or not res.stdout:
            return False
        for line in res.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 2 and parts[0] == target and parts[1].lower() == "device":
                return True
        return False
    except Exception:
        return False


def _adb_connect(target: str) -> bool:
    # Only attempt TCP/IP connect targets like host:port
    if not target or ":" not in target:
        return False
    try:
        res = subprocess.run(["adb", "connect", target], capture_output=True, text=True, check=False)
        out = (res.stdout or "") + (res.stderr or "")
        low = out.lower()
        if "connected to" in low or "already connected to" in low:
            return True
        return False
    except Exception:
        return False


def ensure_adb_connected() -> bool:
    """Best-effort: ensure the configured ADB target is connected. Returns True on success."""
    target = _adb_target()
    if not target:
        return True  # nothing to do
    if _adb_is_connected(target):
        _adb_connection_log_state.record(target, connected=True)
        return True
    log(
        f"[WATCHDOG] ADB target not connected ({target}); attempting adb connect",
        "DEBUG",
    )
    if _adb_connect(target):
        _adb_connection_log_state.record(target, connected=True)
        log(f"[WATCHDOG] adb connect {target}: success", "DEBUG")
        return True
    _adb_connection_log_state.record(target, connected=False)
    log(f"[WATCHDOG] adb connect {target}: failed", "DEBUG")
    return False


def _watchdog_process_check_once() -> None:
    """Run one fail-closed watchdog inspection for the current target."""

    # Serialize against a target migration. Failure to reach ADB is not
    # evidence that the Android game process is absent.
    with ADB_TARGET_OPERATION_LOCK:
        connected = ensure_adb_connected()
        if not connected:
            return
        time.sleep(2)

        # Pause blocks watchdog input/recovery as well as main-loop strategy
        # and handler actions. Connectivity checks may continue so a moved
        # emulator can come back online.
        if AUTOMATION.state in {RunState.PAUSED, RunState.STOPPED}:
            return

        pid_running = _pid_running(GAME_PACKAGE)
        foregrounded = is_game_foregrounded()

        if not pid_running:
            log("[WATCHDOG] Game process not running. Restarting.", "WARN")
            restart_game()
        elif not foregrounded:
            log("[WATCHDOG] Game is backgrounded. Bringing to foreground.", "WARN")
            bring_to_foreground()


def watchdog_process_check(interval=30):
    """
    spec:
      name: watchdog_process_check
      signature: watchdog_process_check(interval:int=30) -> None
      r: null (infinite supervisory loop)
      s: [adb][state][log][loop][sleep]
      e:
        - Catches and logs all Exceptions each cycle; continues looping.
      p:
        interval: Seconds between checks (≥1 recommended).
      notes:
        - Ensures the process is running and foregrounded; calls restart_game or bring_to_foreground as needed.
    """
    while True:
        try:
            _watchdog_process_check_once()

        except Exception as e:
            log(f"[WATCHDOG ERROR] {e}", "ERROR")

        time.sleep(interval)
