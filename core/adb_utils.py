# core/adb_utils.py

import subprocess

#ADB_DEVICE_ID = "07171JEC203290"  # Or ""
ADB_DEVICE_ID = "192.168.1.163:5555"

def adb_shell(cmd, capture_output=False, check=True):
    base_cmd = ["adb"]
    if ADB_DEVICE_ID:
        base_cmd += ["-s", ADB_DEVICE_ID]
    full_cmd = base_cmd + ["shell"] + cmd

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

