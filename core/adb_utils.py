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
from typing import List, Optional, Union

from core.screen_geometry import canonical_to_device_point

#ADB_DEVICE_ID = "07171JEC203290"  # Or ""
ADB_DEVICE_ID = "localhost:5555"


def resolve_adb_device(device_id: Optional[str] = None) -> str:
    """Resolve the active ADB target using the project's established precedence."""

    return device_id or os.getenv("ADB_DEVICE") or ADB_DEVICE_ID


def adb_shell(
    cmd: Union[str, List[str]],
    capture_output: bool = False,
    check: bool = True,
    device_id: Optional[str] = None,
):
    """
    ---
    spec:
      r: "subprocess.CompletedProcess | None"
      s: ["adb"]
      e:
        - "Returns None on CalledProcessError or unexpected Exception (error printed)"
      params:
        cmd: "str or list[str]; string is shlex-split"
        capture_output: "bool — when True, stdout/stderr captured (text=True)"
        check: "bool — if True, non‑zero exit triggers CalledProcessError (caught)"
        device_id: "str|None — explicit device; else env ADB_DEVICE; else module ADB_DEVICE_ID"
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

    Returns:
        subprocess.CompletedProcess on success.
        None on failure (errors printed).
    """
    # Normalize command
    cmd_list = shlex.split(cmd) if isinstance(cmd, str) else cmd

    # Resolve device selection (explicit > env > module default)
    target = resolve_adb_device(device_id)

    base_cmd = ["adb"]
    if target:
        base_cmd += ["-s", target]
    full_cmd = base_cmd + ["shell"] + cmd_list

    try:
        if capture_output:
            result = subprocess.run(
                full_cmd,
                check=check,
                text=True,
                capture_output=True
            )
        else:
            result = subprocess.run(
                full_cmd,
                check=check,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        return result
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB command failed: {e}")
        if hasattr(e, 'stderr') and e.stderr:
            print(f"[STDERR] {e.stderr.strip()}")
        return None
    except Exception as e:
        print(f"[ERROR] Unexpected ADB exception: {e}")
        return None


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
        )
        return result.stdout
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
        )
        return result.stdout
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
):
    """Tap a canonical UI point after mapping it to device framebuffer pixels."""

    target = resolve_adb_device(device_id)
    device_x, device_y = canonical_to_device_point(x, y, device_id=target)
    return adb_shell(
        ["input", "tap", str(device_x), str(device_y)],
        check=check,
        device_id=target,
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
    )


__all__ = [
    "ADB_DEVICE_ID",
    "adb_shell",
    "input_swipe",
    "input_tap",
    "read_device_file",
    "resolve_adb_device",
    "screencap_png",
    "screencap_raw",
]
