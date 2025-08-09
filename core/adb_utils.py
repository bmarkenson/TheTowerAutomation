# core/adb_utils.py
"""
Utility functions for interacting with Android devices or emulators via ADB.

This module provides:
- adb_shell(): Run arbitrary shell commands on a connected device/emulator.
- screencap_png(): Capture a raw PNG screenshot from a connected device/emulator.

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
import shlex
import subprocess
from typing import List, Optional, Union

#ADB_DEVICE_ID = "07171JEC203290"  # Or ""
ADB_DEVICE_ID = "localhost:5555"

def adb_shell(
    cmd: Union[str, List[str]],
    capture_output: bool = False,
    check: bool = True,
    device_id: Optional[str] = None,
):
    """
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
    target = device_id or os.getenv("ADB_DEVICE") or ADB_DEVICE_ID

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


def screencap_png(
    device_id: Optional[str] = None,
    check: bool = True,
) -> Optional[bytes]:
    """
    Capture a screenshot via `adb exec-out screencap -p`.

    Args:
        device_id: Overrides target device. Falls back to env ADB_DEVICE, then ADB_DEVICE_ID.
        check: When True, raises CalledProcessError internally (caught) on non-zero exit.

    Returns:
        PNG bytes on success, or None on failure.
    """
    target = device_id or os.getenv("ADB_DEVICE") or ADB_DEVICE_ID

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
        print(f"[ERROR] ADB screencap failed: {e}")
        if e.stderr:
            try:
                print(f"[STDERR] {e.stderr.decode(errors='ignore').strip()}")
            except Exception:
                pass
        return None
    except Exception as e:
        print(f"[ERROR] Unexpected ADB screencap exception: {e}")
        return None
