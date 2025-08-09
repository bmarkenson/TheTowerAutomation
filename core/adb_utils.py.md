$PROJECT_ROOT/core/adb_utils.py — Library
core.adb_utils.adb_shell(cmd, capture_output=False, check=True, device_id=None) — Returns: subprocess.CompletedProcess (stdout in .stdout when capture_output=True; otherwise output discarded); Side effects: [adb]; Errors: Returns None on CalledProcessError or unexpected Exception (error text printed).
core.adb_utils.screencap_png(device_id=None, check=True) — Returns: PNG bytes from connected device/emulator (or None on failure); Side effects: [adb]; Errors: Returns None when ADB capture fails or returns invalid data; stderr printed on error.
