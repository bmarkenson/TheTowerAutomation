#!/usr/bin/env python3

import argparse
import json
import select
import subprocess
import time
from pathlib import Path

from core.clickmap_access import (
    get_clickmap,
    save_clickmap,
    resolve_dot_path,
    interactive_get_dot_path,
)


JSON_PREFIX = "__GESTURE_JSON__"

# -------------------- Bridge Manager (no globals) --------------------

class ScrcpyBridge:
    """Owns the scrcpy worker process and its stdout stream (JSON gestures)."""

    def __init__(self):
        self.proc = None
        self._script = str((Path(__file__).parent / "scrcpy_adb_input_bridge.py").resolve())

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        print("[INFO] Launching scrcpy_adb_input_bridge.py with --json-stream")
        self.proc = subprocess.Popen(
            ["python3", self._script, "--json-stream"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # we filter by prefix anyway
            text=True,
            bufsize=1,
            encoding="utf-8",
        )

    def ensure_running(self):
        if not self.proc or self.proc.poll() is not None:
            self.start()

    def stop(self):
        if not self.proc:
            return
        print("[INFO] Stopping scrcpy bridge...")
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None

    def __enter__(self):
        self.ensure_running()
        # small settle to let scrcpy window come up; avoid accidental early reads
        time.sleep(2)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()

    def flush_old(self):
        """Discard any buffered gesture JSON lines already emitted by the bridge."""
        if not self.proc or not self.proc.stdout:
            return
        fd = self.proc.stdout.fileno()
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            line = self.proc.stdout.readline()
            if not line:
                break  # EOF
            if line.startswith(JSON_PREFIX):
                # discard stale gesture line
                continue

    def read_gesture(self):
        """Block until a fresh gesture JSON line arrives; return parsed dict."""
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("Bridge not running or stdout unavailable.")
        for line in self.proc.stdout:
            if not line:
                break  # EOF
            line = line.strip()
            if not line.startswith(JSON_PREFIX):
                continue
            payload = line[len(JSON_PREFIX):]
            try:
                return json.loads(payload)
            except json.JSONDecodeError as e:
                print(f"[ERROR] Failed to parse JSON gesture: {e}")
                continue
        raise RuntimeError("Bridge exited before producing gesture")


# -------------------- Utility actions --------------------

def replay_gesture(gesture):
    t = gesture.get("type")
    if t == "tap":
        x, y = gesture["x"], gesture["y"]
        print(f"[REPLAY] tap {x}, {y}")
        subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)])
    elif t == "swipe":
        x1, y1 = gesture["x1"], gesture["y1"]
        x2, y2 = gesture["x2"], gesture["y2"]
        duration = gesture.get("duration_ms", 300)
        print(f"[REPLAY] swipe {x1},{y1} -> {x2},{y2} ({duration}ms)")
        subprocess.run([
            "adb", "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration)
        ])
    else:
        print(f"[REPLAY] Unsupported gesture type: {t}")


# -------------------- Deduped capture/save helpers --------------------

def ensure_entry(dot_path):
    """
    Confirm the clickmap entry exists or interactively create it.
    Returns (clickmap, entry) or (None, None) if user declines.
    """
    clickmap = get_clickmap()
    entry = resolve_dot_path(dot_path)
    if entry is None:
        print(f"[WARN] Entry '{dot_path}' does not exist.")
        confirm = input(f"Create new gesture entry at '{dot_path}'? (y/N): ").strip().lower()
        if confirm != 'y':
            return None, None
        group, key = dot_path.split(".", 1)
        clickmap.setdefault(group, {})[key] = {"roles": ["gesture"]}
        entry = clickmap[group][key]
    return clickmap, entry


def record_and_save(bridge: ScrcpyBridge, dot_path: str):
    """
    Common flow: ensure entry, flush, capture one gesture, validate, save, optional replay.
    """
    clickmap, entry = ensure_entry(dot_path)
    if not entry:
        print("[INFO] Gesture not saved.")
        return

    print(f"[INFO] Recording gesture for '{dot_path}' — perform the gesture now.")
    bridge.flush_old()
    gesture = bridge.read_gesture()

    t = gesture.get("type")
    if t not in ("tap", "swipe"):
        print(f"[ERROR] Unsupported gesture type: {t}")
        return

    entry[t] = gesture
    save_clickmap(clickmap)
    print(f"[INFO] Gesture saved under '{dot_path}'")

    replay = input("Replay this gesture? (Y/n): ").strip().lower()
    if replay in ("", "y", "yes"):
        replay_gesture(gesture)


# -------------------- Main --------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="dot_path to save gesture under")
    args = parser.parse_args()

    with ScrcpyBridge() as bridge:
        try:
            if args.name:
                record_and_save(bridge, args.name)
            else:
                print("[INFO] Interactive mode. Press Ctrl+C to exit.")
                while True:
                    dot_path = interactive_get_dot_path(get_clickmap())
                    if not dot_path:
                        print("[INFO] No path selected. Exiting.")
                        break
                    record_and_save(bridge, dot_path)

        except KeyboardInterrupt:
            print("\n[INFO] Exiting gesture logger.")

if __name__ == "__main__":
    main()
