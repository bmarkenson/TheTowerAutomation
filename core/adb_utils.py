# core/adb_utils.py
"""
Utility functions for interacting with Android devices or emulators via ADB.

This module provides:
- adb_shell(): Run arbitrary shell commands on a connected device/emulator.
- read_device_file(): Read one device file through ADB without modifying it.
- screencap_png(): Capture a raw PNG screenshot from a connected device/emulator.
- screencap_raw(): Capture the Android raw framebuffer.
- input_tap()/input_swipe(): Map canonical UI coordinates to the observed
  device resolution before injecting input.

Device targeting:
    Functions respect the following precedence when selecting a device:
    1. Explicit `device_id` argument
    2. Environment variable ADB_DEVICE
    3. Module constant ADB_DEVICE_ID

Dependencies:
    - Requires `adb` to be installed and on the system PATH.
    - No heavy dependencies (e.g., OpenCV) are imported here.
      PNG decoding and image handling should be done in higher-level modules
      such as core/ss_capture.py.
"""

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Union

from core.run_state import AUTOMATION
from core.screen_geometry import canonical_to_device_point

#ADB_DEVICE_ID = "07171JEC203290"  # Or ""
ADB_DEVICE_ID = "localhost:5555"
ADB_SHELL_TIMEOUT_SECONDS = 10.0
ADB_SCREENSHOT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class AdbShellDispatchOutcome:
    """Typed host/device boundary result for lifecycle-safe callers."""

    result: Any = None
    attempted: bool = False
    uncertain: bool = False

    @property
    def accepted(self) -> bool:
        return self.result is not None

    def __bool__(self) -> bool:
        return self.accepted


_READ_ONLY_SHELL_COMMANDS = frozenset(
    {
        ("dumpsys", "window", "windows"),
        ("dumpsys", "activity", "activities"),
        ("ps", "-A"),
        ("service", "call", "clipboard", "3", "s16", "com.android.shell"),
    }
)


def _shell_command_is_read_only(cmd: List[str]) -> bool:
    """Return whether a known shell command is passive runtime observation."""

    normalized = tuple(str(value).strip() for value in cmd)
    if normalized in _READ_ONLY_SHELL_COMMANDS:
        return True
    return bool(
        len(normalized) == 2
        and normalized[0] == "pidof"
        and re.fullmatch(r"[A-Za-z0-9._]+", normalized[1]) is not None
    )


def resolve_adb_device(device_id: Optional[str] = None) -> str:
    """Resolve the active ADB target using the project's established precedence."""

    return device_id or os.getenv("ADB_DEVICE") or ADB_DEVICE_ID


def adb_shell(
    cmd: Union[str, List[str]],
    capture_output: bool = False,
    check: bool = True,
    device_id: Optional[str] = None,
    report_errors: bool = True,
    timeout_s: float = ADB_SHELL_TIMEOUT_SECONDS,
    action_guard_fn: Optional[Callable[[], bool]] = None,
    return_dispatch_outcome: bool = False,
    defer_uncertain_reporting: bool = False,
):
    """
    ---
    spec:
      r: "subprocess.CompletedProcess | None"
      s: ["adb"]
      e:
        - "Returns None on CalledProcessError or unexpected Exception"
      params:
        cmd: "str or list[str]; string is shlex-split"
        capture_output: "bool — when True, stdout/stderr captured (text=True)"
        check: "bool — if True, non‑zero exit triggers CalledProcessError (caught)"
        device_id: "str|None — explicit device; else env ADB_DEVICE; else module ADB_DEVICE_ID"
        report_errors: "bool — print subprocess failures when True"
      notes:
        - "stdout/stderr suppressed when capture_output=False"
        - "Uses 'adb -s <target> shell …'"
    ---
    Run an ADB shell command.

    Args:
        cmd: Either a list of args (preferred) or a single string (split via shlex).
        capture_output: When True, returns stdout/stderr in the CompletedProcess.
        check: When True, raises CalledProcessError internally (caught below) on non-zero exit.
        device_id: Overrides target device. Falls back to env ADB_DEVICE, then ADB_DEVICE_ID.
        report_errors: Print subprocess failures when True.
        timeout_s: Bound the host-side ADB shell dispatch.
        action_guard_fn: Optional narrower authority checked atomically with a
            mutating dispatch.
        return_dispatch_outcome: Return typed attempted/uncertain metadata for
            lifecycle callers instead of the legacy process result.
        defer_uncertain_reporting: Let a lifecycle transaction owner report
            only if its required final restoration cannot be proven.

    Returns:
        subprocess.CompletedProcess on success.
        None on failure (errors printed when ``report_errors`` is true).
    """
    # Normalize command
    cmd_list = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)

    # Resolve device selection (explicit > env > module default)
    target = resolve_adb_device(device_id)

    base_cmd = ["adb"]
    if target:
        base_cmd += ["-s", target]
    full_cmd = base_cmd + ["shell"] + cmd_list
    read_only = _shell_command_is_read_only(cmd_list)
    timeout = max(0.1, float(timeout_s))

    def dispatch() -> AdbShellDispatchOutcome:
        try:
            if capture_output:
                return AdbShellDispatchOutcome(
                    result=subprocess.run(
                        full_cmd,
                        check=check,
                        text=True,
                        capture_output=True,
                        timeout=timeout,
                    ),
                    attempted=True,
                )
            return AdbShellDispatchOutcome(
                result=subprocess.run(
                    full_cmd,
                    check=check,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                ),
                attempted=True,
            )
        except subprocess.TimeoutExpired:
            if not read_only and not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation timed out after dispatch: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    f"target={target} timeout_s={timeout:.1f}"
                )
            if report_errors:
                print(
                    "[ERROR] ADB command timed out: "
                    f"{cmd_list[0] if cmd_list else 'unknown'}"
                )
            return AdbShellDispatchOutcome(
                attempted=True,
                uncertain=not read_only,
            )
        except subprocess.CalledProcessError as e:
            if not read_only and not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation returned a nonzero result after dispatch: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    f"target={target} returncode={e.returncode}"
                )
            if report_errors:
                print(f"[ERROR] ADB command failed: {e}")
                if hasattr(e, 'stderr') and e.stderr:
                    print(f"[STDERR] {e.stderr.strip()}")
            return AdbShellDispatchOutcome(
                attempted=True,
                uncertain=not read_only,
            )
        except (FileNotFoundError, PermissionError, TypeError, ValueError) as e:
            # These narrow failures prove that the host could not construct or
            # start the requested ADB client.  Broader OSError values are not
            # treated this way because they can also arise while waiting for
            # an already-started child.
            if report_errors:
                print(f"[ERROR] ADB command could not start: {e}")
            return AdbShellDispatchOutcome()
        except OSError as e:
            if not read_only and not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation raised an OS error after dispatch may have "
                    "started: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    f"target={target} error={type(e).__name__}"
                )
            if report_errors:
                print(f"[ERROR] ADB command OS failure: {e}")
            return AdbShellDispatchOutcome(
                attempted=True,
                uncertain=not read_only,
            )
        except Exception as e:
            if not read_only and not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation raised after dispatch was attempted: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    f"target={target} error={type(e).__name__}"
                )
            if report_errors:
                print(f"[ERROR] Unexpected ADB exception: {e}")
            return AdbShellDispatchOutcome(
                attempted=True,
                uncertain=not read_only,
            )
        except BaseException as exc:
            # KeyboardInterrupt/SystemExit can land after the ADB client has
            # delivered input.  Preserve graceful shutdown, but first retain a
            # catastrophic local hold for an outcome that cannot be proven.
            if not read_only and not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation was interrupted after dispatch may have "
                    "started: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    f"target={target} interruption={type(exc).__name__}"
                )
            raise

    def return_value(outcome: AdbShellDispatchOutcome):
        return outcome if return_dispatch_outcome else outcome.result

    if read_only:
        return return_value(dispatch())

    # Unknown shell commands fail on the safe side: when a production runtime
    # guard is installed, only the explicit read-only allowlist bypasses the
    # global device-mutation boundary.  The boundary refreshes durable control
    # intent and remains held through the complete subprocess dispatch.
    with AUTOMATION.authorize_mutation(action_guard_fn) as allowed:
        if not allowed:
            return return_value(AdbShellDispatchOutcome())
        outcome = dispatch()
        if (
            outcome.result is not None
            and int(getattr(outcome.result, "returncode", 0)) != 0
        ):
            if not defer_uncertain_reporting:
                AUTOMATION.report_uncertain_mutation(
                    "ADB mutation returned a nonzero result after dispatch: "
                    f"command={str(cmd_list[0] if cmd_list else 'unknown')} "
                    "target="
                    f"{target} returncode="
                    f"{getattr(outcome.result, 'returncode', 'unknown')}"
                )
            outcome = AdbShellDispatchOutcome(
                attempted=True,
                uncertain=True,
            )
        return return_value(outcome)


def read_device_file(
    path: str,
    *,
    device_id: Optional[str] = None,
    check: bool = True,
    timeout_s: float = 10.0,
    report_errors: bool = True,
) -> Optional[bytes]:
    """Read one absolute device path with ``adb exec-out cat``.

    ADB executes the command through the device shell, so the path is limited
    to a shell-inert Android path grammar before it is passed to ``cat``.  This
    is a read-only transport primitive; callers remain responsible for
    validating the returned format and obtaining a stable copy when the device
    may be writing the file.
    """

    normalized = str(path or "").strip()
    if (
        re.fullmatch(r"/[A-Za-z0-9._+/-]+", normalized) is None
        or ".." in normalized.split("/")
    ):
        raise ValueError("device file path must be absolute and shell-inert")
    target = resolve_adb_device(device_id)
    command = ["adb"]
    if target:
        command += ["-s", target]
    command += ["exec-out", "cat", normalized]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=max(0.1, float(timeout_s)),
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        if report_errors:
            print(f"[ERROR] ADB device-file read failed: {exc}")
        return None


def screencap_png(
    device_id: Optional[str] = None,
    check: bool = True,
    report_errors: bool = True,
    timeout_s: float = ADB_SCREENSHOT_TIMEOUT_SECONDS,
) -> Optional[bytes]:
    """
    ---
    spec:
      r: "bytes | None (PNG)"
      s: ["adb"]
      e:
        - "Returns None on non-zero exit or invalid/empty data; error printed"
      params:
        device_id: "str|None — explicit device; else env ADB_DEVICE; else module ADB_DEVICE_ID"
        check: "bool — if True, non‑zero exit raises CalledProcessError (caught)"
        report_errors: "bool — print subprocess failures when True"
      notes:
        - "Uses 'adb exec-out screencap -p'"
    ---
    Capture a screenshot via `adb exec-out screencap -p`.

    Args:
        device_id: Overrides target device. Falls back to env ADB_DEVICE, then ADB_DEVICE_ID.
        check: When True, raises CalledProcessError internally (caught) on non-zero exit.
        timeout_s: Bound the host-side ADB screenshot subprocess.

    Returns:
        PNG bytes on success, or None on failure.
    """
    target = resolve_adb_device(device_id)

    base_cmd = ["adb"]
    if target:
        base_cmd += ["-s", target]
    full_cmd = base_cmd + ["exec-out", "screencap", "-p"]

    try:
        result = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=max(0.1, float(timeout_s)),
        )
        return result.stdout
    except subprocess.TimeoutExpired as e:
        if report_errors:
            print(f"[ERROR] ADB screencap timed out: {e}")
        return None
    except subprocess.CalledProcessError as e:
        # Mirror adb_shell's lightweight reporting without pulling in logger here.
        if report_errors:
            print(f"[ERROR] ADB screencap failed: {e}")
        if report_errors and e.stderr:
            try:
                print(f"[STDERR] {e.stderr.decode(errors='ignore').strip()}")
            except Exception:
                pass
        return None
    except Exception as e:
        if report_errors:
            print(f"[ERROR] Unexpected ADB screencap exception: {e}")
        return None


def screencap_raw(
    device_id: Optional[str] = None,
    check: bool = True,
    timeout_s: float = ADB_SCREENSHOT_TIMEOUT_SECONDS,
) -> Optional[bytes]:
    """Capture the Android raw framebuffer without device-side PNG encoding."""

    target = resolve_adb_device(device_id)

    base_cmd = ["adb"]
    if target:
        base_cmd += ["-s", target]
    full_cmd = base_cmd + ["exec-out", "screencap"]

    try:
        result = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=max(0.1, float(timeout_s)),
        )
        return result.stdout
    except subprocess.TimeoutExpired as exc:
        print(f"[ERROR] ADB raw screencap timed out: {exc}")
        return None
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] ADB raw screencap failed: {exc}")
        if exc.stderr:
            try:
                print(f"[STDERR] {exc.stderr.decode(errors='ignore').strip()}")
            except Exception:
                pass
        return None
    except Exception as exc:
        print(f"[ERROR] Unexpected ADB raw screencap exception: {exc}")
        return None


def input_tap(
    x: int | float,
    y: int | float,
    *,
    check: bool = True,
    device_id: Optional[str] = None,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """Tap a canonical UI point after mapping it to device framebuffer pixels."""

    target = resolve_adb_device(device_id)
    device_x, device_y = canonical_to_device_point(x, y, device_id=target)
    guard_kwargs = (
        {"action_guard_fn": action_guard_fn}
        if action_guard_fn is not None
        else {}
    )
    return adb_shell(
        ["input", "tap", str(device_x), str(device_y)],
        check=check,
        device_id=target,
        **guard_kwargs,
    )


def input_swipe(
    x1: int | float,
    y1: int | float,
    x2: int | float,
    y2: int | float,
    duration_ms: int,
    *,
    check: bool = True,
    device_id: Optional[str] = None,
    action_guard_fn: Optional[Callable[[], bool]] = None,
):
    """Swipe between canonical UI points in device framebuffer pixels."""

    target = resolve_adb_device(device_id)
    device_x1, device_y1 = canonical_to_device_point(
        x1,
        y1,
        device_id=target,
    )
    device_x2, device_y2 = canonical_to_device_point(
        x2,
        y2,
        device_id=target,
    )
    guard_kwargs = (
        {"action_guard_fn": action_guard_fn}
        if action_guard_fn is not None
        else {}
    )
    return adb_shell(
        [
            "input",
            "swipe",
            str(device_x1),
            str(device_y1),
            str(device_x2),
            str(device_y2),
            str(int(duration_ms)),
        ],
        check=check,
        device_id=target,
        **guard_kwargs,
    )


__all__ = [
    "ADB_DEVICE_ID",
    "ADB_SCREENSHOT_TIMEOUT_SECONDS",
    "ADB_SHELL_TIMEOUT_SECONDS",
    "AdbShellDispatchOutcome",
    "adb_shell",
    "input_swipe",
    "input_tap",
    "read_device_file",
    "resolve_adb_device",
    "screencap_png",
    "screencap_raw",
]
