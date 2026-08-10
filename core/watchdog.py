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

from contextlib import contextmanager
import re
import threading
import time
from typing import Callable, Iterator, Optional

from core.run_state import AUTOMATION, RunState
from core.adb_connection import (
    AdbConnectionCoordinator,
    DEFAULT_ADB_CONNECTION_COORDINATOR,
)
from core.adb_utils import adb_shell
from core.adb_target_session import ADB_TARGET_OPERATION_LOCK
from utils.logger import log, log_input

GAME_PACKAGE = "com.TechTreeGames.TheTower"
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


class CooperativeMutationGuard:
    """Serialize watchdog mutations against a production quiescence boundary.

    Passive watchdog observations do not use this lock.  A mutating recovery
    holds it from its final authority check until the mutation finishes, while
    hold installation acquires the same lock before publishing quiescence.
    """

    def __init__(self, action_allowed: Callable[[], bool]):
        self._action_allowed = action_allowed
        self._lock = threading.RLock()

    @contextmanager
    def authorize_mutation(self) -> Iterator[bool]:
        """Yield one final fail-closed authority decision under the guard."""

        with self._lock:
            try:
                allowed = bool(self._action_allowed())
            except Exception as exc:
                log(
                    f"[WATCHDOG] Mutating recovery authority check failed: {exc}",
                    "ERROR",
                )
                allowed = False
            yield allowed

    @contextmanager
    def quiescence_boundary(self) -> Iterator[None]:
        """Wait for an authorized mutation to finish and exclude new ones."""

        with self._lock:
            yield


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


def bring_to_foreground(*, input_reason: Optional[str] = None) -> bool:
    """
    spec:
      name: bring_to_foreground
      signature: bring_to_foreground(*, input_reason: str|None = None) -> bool
      r: True only when Android accepted the bounded launcher command.
      s: [adb][log][sleep]
      e: none (uses check=False; best-effort)
      notes:
        - Sends a single monkey LAUNCHER intent for GAME_PACKAGE and waits ~5s.
    """
    if input_reason:
        log_input(
            "Launched The Tower through Android",
            detail=(
                f"ADB_SHELL monkey package={GAME_PACKAGE} "
                f"reason={' '.join(str(input_reason).split())[:256]}"
            ),
        )
    result = adb_shell([
        "monkey", "-p", GAME_PACKAGE,
        "-c", "android.intent.category.LAUNCHER", "1"
    ], capture_output=True, check=False)
    if result is None or result.returncode != 0:
        log(
            "[WATCHDOG] The Tower launcher command was not accepted",
            "DEBUG",
        )
        return False
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)
    return True


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


def _dispatch_mutating_recovery(
    action: Callable[[], None],
    *,
    warning: str,
    mutation_guard: Optional[CooperativeMutationGuard],
) -> bool:
    """Run one watchdog mutation only under a final cooperative check."""

    if mutation_guard is None:
        if AUTOMATION.state in {RunState.PAUSED, RunState.STOPPED}:
            return False
        log(warning, "WARN")
        action()
        return True

    with mutation_guard.authorize_mutation() as allowed:
        # Keep operator control authoritative even if a caller supplies a
        # permissive or stale callback.  This is the final check immediately
        # before the mutating dispatch.
        if (
            not allowed
            or AUTOMATION.state in {RunState.PAUSED, RunState.STOPPED}
        ):
            return False
        log(warning, "WARN")
        action()
        return True


def _watchdog_process_check_once(
    connection_coordinator: AdbConnectionCoordinator = (
        DEFAULT_ADB_CONNECTION_COORDINATOR
    ),
    mutation_guard: Optional[CooperativeMutationGuard] = None,
) -> None:
    """Run one fail-closed watchdog inspection for the current target."""

    # Serialize against a target migration. Failure to reach ADB is not
    # evidence that the Android game process is absent.
    with ADB_TARGET_OPERATION_LOCK:
        connected = connection_coordinator.ensure_connected()
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
            _dispatch_mutating_recovery(
                restart_game,
                warning="[WATCHDOG] Game process not running. Restarting.",
                mutation_guard=mutation_guard,
            )
        elif not foregrounded:
            _dispatch_mutating_recovery(
                bring_to_foreground,
                warning=(
                    "[WATCHDOG] Game is backgrounded. Bringing to foreground."
                ),
                mutation_guard=mutation_guard,
            )


def watchdog_process_check(
    interval=30,
    connection_coordinator: AdbConnectionCoordinator = (
        DEFAULT_ADB_CONNECTION_COORDINATOR
    ),
    mutation_guard: Optional[CooperativeMutationGuard] = None,
):
    """
    spec:
      name: watchdog_process_check
      signature: watchdog_process_check(interval:int=30, mutation_guard=None) -> None
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
            _watchdog_process_check_once(
                connection_coordinator,
                mutation_guard,
            )

        except Exception as e:
            log(f"[WATCHDOG ERROR] {e}", "ERROR")

        time.sleep(interval)
