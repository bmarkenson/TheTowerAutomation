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
import time
from typing import Callable, Iterator, Optional

from core.run_state import AUTOMATION, RunState
from core.adb_connection import (
    AdbConnectionCoordinator,
    DEFAULT_ADB_CONNECTION_COORDINATOR,
)
from core.adb_utils import AdbShellDispatchOutcome, adb_shell
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

    def _allowed(self) -> bool:
        try:
            return bool(self._action_allowed())
        except Exception as exc:
            log(
                f"[WATCHDOG] Mutating recovery authority check failed: {exc}",
                "ERROR",
            )
            return False

    @contextmanager
    def authorize_mutation(self) -> Iterator[bool]:
        """Yield one final fail-closed authority decision under the guard."""

        with AUTOMATION.authorize_mutation(
            self._allowed,
            defer_dispatch_boundary=True,
        ) as allowed:
            yield allowed

    def refresh_mutation_authority(self) -> bool:
        """Consume a Pause persisted during passive recovery revalidation."""

        return AUTOMATION.refresh_mutation_authority(self._allowed)

    @contextmanager
    def quiescence_boundary(self) -> Iterator[None]:
        """Wait for an authorized mutation to finish and exclude new ones."""

        with AUTOMATION.quiescence_boundary():
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


def bring_to_foreground(
    *,
    input_reason: Optional[str] = None,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    return_dispatch_outcome: bool = False,
    defer_uncertain_reporting: bool = False,
) -> bool | AdbShellDispatchOutcome:
    """
    spec:
      name: bring_to_foreground
      signature: bring_to_foreground(*, input_reason: str|None = None,
                 action_guard_fn=None, return_dispatch_outcome=False,
                 defer_uncertain_reporting=False)
      r: True only when Android accepted the bounded launcher command.
      s: [adb][log][sleep]
      e: none (uses check=False; best-effort)
      notes:
        - Sends a single monkey LAUNCHER intent for GAME_PACKAGE and waits ~5s.
    """
    dispatch_options: dict[str, object] = {
        "check": False,
        "return_dispatch_outcome": True,
    }
    if action_guard_fn is not None:
        dispatch_options["action_guard_fn"] = action_guard_fn
    if defer_uncertain_reporting:
        dispatch_options["defer_uncertain_reporting"] = True
    outcome = adb_shell(
        [
            "monkey", "-p", GAME_PACKAGE,
            "-c", "android.intent.category.LAUNCHER", "1",
        ],
        **dispatch_options,
    )
    if input_reason and bool(getattr(outcome, "attempted", False)):
        log_input(
            "Launched The Tower through Android",
            detail=(
                f"ADB_SHELL monkey package={GAME_PACKAGE} "
                f"reason={' '.join(str(input_reason).split())[:256]} "
                f"accepted={bool(getattr(outcome, 'accepted', False))} "
                f"uncertain={bool(getattr(outcome, 'uncertain', False))}"
            ),
        )
    if not bool(getattr(outcome, "accepted", False)):
        log(
            "[WATCHDOG] The Tower launcher command was not accepted",
            "DEBUG",
        )
        return outcome if return_dispatch_outcome else False
    log("[WATCHDOG] Sent monkey event to foreground game.", "INFO")
    time.sleep(5)
    return outcome if return_dispatch_outcome else True


def restart_game() -> bool:
    """
    spec:
      name: restart_game
      signature: restart_game() -> None
      r: null
      s: [adb][log][sleep]
      e: none (best-effort; uses check=False)
      notes:
        - Force-stops GAME_PACKAGE and relaunches it as one guarded mutation.
        - Sleeps ~6s after relaunch to allow surface creation.
    """
    log("[WATCHDOG] Restarting game via monkey intent", "INFO")

    # Hard-stop first to avoid stale process/session on emulators
    pending_interrupt: Optional[BaseException] = None
    try:
        stopped = adb_shell(
            ["am", "force-stop", GAME_PACKAGE],
            check=False,
            return_dispatch_outcome=True,
            defer_uncertain_reporting=True,
        )
        stop_attempted = bool(getattr(stopped, "attempted", False))
        stop_accepted = bool(getattr(stopped, "accepted", False))
    except BaseException as exc:
        # The child may have delivered force-stop before interruption. Keep
        # shutdown pending until launcher restoration has been attempted.
        pending_interrupt = exc
        stop_attempted = True
        stop_accepted = False
    if not stop_accepted and not stop_attempted:
        return False

    # Launch the game (monkey keeps us activity-agnostic). An interrupted or
    # uncertain first launch gets one bounded retry while the outer mutation
    # transaction retains Pause exclusion and exact-target ownership.
    launched = False
    for _attempt in range(2):
        try:
            launch_outcome = adb_shell(
                [
                    "monkey", "-p", GAME_PACKAGE,
                    "-c", "android.intent.category.LAUNCHER", "1",
                ],
                check=False,
                return_dispatch_outcome=True,
                defer_uncertain_reporting=True,
            )
        except BaseException as exc:
            if pending_interrupt is None:
                pending_interrupt = exc
            continue
        if bool(getattr(launch_outcome, "accepted", False)):
            launched = True
            break
    if not launched:
        # Force-stop may already have completed.  A launcher failure is now a
        # failed mandatory restoration, even when host provenance proves a
        # launcher attempt itself never started.
        AUTOMATION.report_uncertain_mutation(
            "Watchdog restart may have stopped The Tower but launcher "
            "restoration was not accepted"
        )
        if pending_interrupt is not None:
            raise pending_interrupt.with_traceback(
                pending_interrupt.__traceback__
            )
        return False
    time.sleep(6)

    log("[WATCHDOG] Game launched — deferring to main loop for state detection", "INFO")
    if pending_interrupt is not None:
        raise pending_interrupt.with_traceback(pending_interrupt.__traceback__)
    return True


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
    action: Callable[[], object],
    *,
    warning: str,
    mutation_guard: Optional[CooperativeMutationGuard],
    action_still_required: Callable[[], bool] = lambda: True,
) -> bool:
    """Run one watchdog mutation only under a final cooperative check."""

    guard = mutation_guard or CooperativeMutationGuard(
        lambda: AUTOMATION.state is RunState.RUNNING
    )
    with guard.authorize_mutation() as allowed:
        # Keep operator control authoritative even if a caller supplies a
        # permissive or stale callback.  This is the final check immediately
        # before the mutating dispatch.
        if (
            not allowed
            or AUTOMATION.state in {RunState.PAUSED, RunState.STOPPED}
        ):
            return False
        # Canonical lock order is mutation -> exact target. Revalidate the
        # observed process/foreground condition while target handoff is
        # excluded, then retain both locks through lifecycle dispatch.
        with ADB_TARGET_OPERATION_LOCK:
            if not action_still_required():
                return False
            log(warning, "WARN")
            # The passive revalidation above may itself overlap or trigger a
            # newly persisted Pause.  Re-read the runtime-owned durable guard
            # at the last point before the first lifecycle dispatch.
            if not guard.refresh_mutation_authority():
                return False
            return action() is not False


def _watchdog_process_check_once(
    connection_coordinator: AdbConnectionCoordinator = (
        DEFAULT_ADB_CONNECTION_COORDINATOR
    ),
    mutation_guard: Optional[CooperativeMutationGuard] = None,
) -> None:
    """Run one fail-closed watchdog inspection for the current target."""

    # Passive observation may hold the target lock without mutation authority.
    # If recovery is indicated, release it before acquiring the mutation
    # boundary and reacquire it only in canonical mutation -> target order.
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
            action_still_required=lambda: bool(
                connection_coordinator.ensure_connected()
                and AUTOMATION.state is RunState.RUNNING
                and not _pid_running(GAME_PACKAGE)
            ),
        )
    elif not foregrounded:
        _dispatch_mutating_recovery(
            bring_to_foreground,
            warning=(
                "[WATCHDOG] Game is backgrounded. Bringing to foreground."
            ),
            mutation_guard=mutation_guard,
            action_still_required=lambda: bool(
                connection_coordinator.ensure_connected()
                and AUTOMATION.state is RunState.RUNNING
                and _pid_running(GAME_PACKAGE)
                and not is_game_foregrounded()
            ),
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
