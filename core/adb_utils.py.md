core/adb_utils.py
core.adb_utils.adb_shell(cmd, capture_output=False, check=True, device_id=None) — R: subprocess.CompletedProcess (stdout in .stdout when capture_output=True; otherwise output discarded); S: [adb]; E: Returns None on CalledProcessError or unexpected Exception (error text printed).
core.adb_utils.screencap_png(device_id=None, check=True) — R: PNG bytes from connected device/emulator (or None on failure); S: [adb]; E: Returns None when ADB capture fails or returns invalid data; stderr printed on error.
