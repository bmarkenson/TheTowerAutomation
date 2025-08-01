#!/usr/bin/env python3
# log_meminfo.py
import subprocess
import time
import re
from datetime import datetime
from pathlib import Path

PACKAGE = "com.TechTreeGames.TheTower"
INTERVAL = 600  # 10 minutes
LOGFILE = "logs/meminfo.log"

Path("logs").mkdir(exist_ok=True)

THERMAL_LOG = "logs/thermal.log"

def get_thermal_snapshot():
    try:
        result = subprocess.run(
            ["adb", "shell", "dumpsys", "thermalservice"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        temps = {}
        recording = False
        for line in result.stdout.splitlines():
            if "Current temperatures from HAL:" in line:
                recording = True
                continue
            if recording:
                if not line.strip():
                    break  # end of section
                match = re.search(r'Temperature\{mValue=([\d.]+), .*?mName=([a-zA-Z0-9\-_:]+),', line)
                if match:
                    temp = float(match.group(1))
                    label = match.group(2)
                    temps[label] = temp
        return temps
    except Exception as e:
        return {"error": str(e)}

def dump_logcat(ts_label):
    filename = f"logs/logcat_{ts_label}.txt"
    try:
        with open(filename, "w") as f:
            subprocess.run(
                ["adb", "logcat", "-b", "all", "-v", "time", "-d"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=10
            )
        return filename
    except Exception as e:
        return f"FAILED_LOGCAT: {e}"

def get_meminfo():
    try:
        result = subprocess.run(
            ["adb", "shell", "dumpsys", "meminfo", PACKAGE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        lines = result.stdout.splitlines()
        summary = {}
        for line in lines:
            if "TOTAL PSS" in line:
                match = re.search(r'TOTAL PSS:\s+(\d+)', line)
                if match:
                    summary["TOTAL_PSS"] = int(match.group(1))
            elif "Private Other:" in line:
                match = re.search(r'Private Other:\s+(\d+)', line)
                if match:
                    summary["Private_Other"] = int(match.group(1))
            elif "Graphics:" in line:
                match = re.search(r'Graphics:\s+(\d+)', line)
                if match:
                    summary["Graphics"] = int(match.group(1))
            elif "Java Heap:" in line:
                match = re.search(r'Java Heap:\s+(\d+)', line)
                if match:
                    summary["Java_Heap"] = int(match.group(1))
            elif "Native Heap:" in line:
                match = re.search(r'Native Heap:\s+(\d+)', line)
                if match:
                    summary["Native_Heap"] = int(match.group(1))
        return summary
    except Exception as e:
        return {"error": str(e)}

def get_sys_meminfo():
    try:
        result = subprocess.run(
            ["adb", "shell", "cat", "/proc/meminfo"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        sysinfo = {}
        for line in result.stdout.splitlines():
            match = re.match(r"(\w+):\s+(\d+)", line)
            if match:
                sysinfo[match.group(1)] = int(match.group(2))  # kB
        return {
            "MemFree": sysinfo.get("MemFree"),
            "MemAvailable": sysinfo.get("MemAvailable"),
            "SwapTotal": sysinfo.get("SwapTotal"),
            "SwapFree": sysinfo.get("SwapFree")
        }
    except Exception as e:
        return {"sys_error": str(e)}

def get_uptime():
    try:
        result = subprocess.run(
            ["adb", "shell", "cat", "/proc/uptime"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        parts = result.stdout.strip().split()
        if len(parts) >= 1:
            uptime_sec = float(parts[0])
            return int(uptime_sec)
    except:
        pass
    return -1

def get_boot_completed():
    try:
        result = subprocess.run(
            ["adb", "shell", "getprop", "sys.boot_completed"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.stdout.strip()
    except:
        return "?"

def get_max_temp():
    try:
        result = subprocess.run(
            ["adb", "shell", "cat", "/sys/class/thermal/thermal_zone*/temp"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        temps = [int(t.strip()) for t in result.stdout.splitlines() if t.strip().isdigit()]
        if temps:
            return max(temps) / 1000.0  # most devices report in millidegrees
    except:
        pass
    return -1

def main():
    print("[INFO] Starting meminfo logger.")
    last_uptime = None
    with open(LOGFILE, "a") as f:
        while True:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ts_label = ts.replace(":", "-").replace(" ", "_")

            data = get_meminfo()
            sys = get_sys_meminfo()
            uptime = get_uptime()
            boot = get_boot_completed()

            logcat_triggered = False
            line = f"[{ts}]"

            thermal_data = get_thermal_snapshot()
            if "error" in thermal_data:
                max_temp = -1.0
                thermal_line = f"[{ts}] ERROR: {thermal_data['error']}"
            else:
                max_temp = max(thermal_data.values()) if thermal_data else -1.0
                thermal_line = f"[{ts}] " + " ".join([f"{k}={v:.1f}°C" for k, v in thermal_data.items()])
            
            # Write separate thermal log
            with open(THERMAL_LOG, "a") as tf:
                tf.write(thermal_line + "\n")
            
            line += f" | MaxTemp={max_temp:.1f}°C"

            # Trigger if adb meminfo failed
            if "error" in data:
                line += f" ERROR: {data['error']}"
                logcat_triggered = True
            else:
                line += (
                    f" PSS={data.get('TOTAL_PSS')} PrivateOther={data.get('Private_Other')}"
                    f" Graphics={data.get('Graphics')} JavaHeap={data.get('Java_Heap')} NativeHeap={data.get('Native_Heap')}"
                )

            if "sys_error" in sys:
                line += f" | SYS ERROR: {sys['sys_error']}"
                logcat_triggered = True
            else:
                line += (
                    f" | MemFree={sys.get('MemFree')}kB MemAvailable={sys.get('MemAvailable')}kB"
                    f" SwapFree={sys.get('SwapFree')}kB SwapTotal={sys.get('SwapTotal')}kB"
                )

            line += f" | Uptime={uptime}s BootCompleted={boot}"

            # Check for uptime reset (possible crash)
            if last_uptime is not None and uptime != -1 and uptime < last_uptime:
                line += " | DETECTED: Reboot (uptime decreased)"
                logcat_triggered = True

            if logcat_triggered:
                logcat_file = dump_logcat(ts_label)
                line += f" | LogcatDumped={logcat_file}"

            print(line)
            f.write(line + "\n")
            f.flush()
            last_uptime = uptime
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

