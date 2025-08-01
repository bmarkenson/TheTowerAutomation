#!/usr/bin/env python3

import os
import time
import json
import subprocess
import keyboard
from core.clickmap_access import tap_now get_clickmap

clickmap = get_clickmap()

def load_clickmap():
    with open(clickmap, "r") as f:
        return json.load(f)

def save_clickmap(data):
    tmp = clickmap + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, clickmap)
    print("[INFO] Saved changes.")

def run_adb_swipe(x1, y1, x2, y2, duration):
    subprocess.run([
        "adb", "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2), str(duration)
    ])

def choose_gesture(clickmap):
    print("Available gestures:")
    entries = list(clickmap.items())
    for idx, (key, val) in enumerate(entries, 1):
        gtype = "swipe" if "swipe" in val else ("tap" if "tap" in val else "unknown")
        print(f"[{idx}] {key:20} ({gtype})")
    while True:
        try:
            i = int(input("Enter gesture number: ")) - 1
            if 0 <= i < len(entries):
                return entries[i][0], entries[i][1]
        except ValueError:
            continue

def edit_swipe(name, swipe_entry):
    print(f"\nEditing: {name}")
    print_controls()
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"\nEditing: {name}")
        print(f"Start:   (x1={swipe_entry['x1']}, y1={swipe_entry['y1']})")
        print(f"End:     (x2={swipe_entry['x2']}, y2={swipe_entry['y2']})")
        print(f"Duration: {swipe_entry['duration_ms']} ms\n")
        print_controls()

        event = keyboard.read_event()
        if event.event_type != keyboard.KEY_DOWN:
            continue
        key = event.name

        if key == "left": swipe_entry["x2"] -= 10
        elif key == "right": swipe_entry["x2"] += 10
        elif key == "up": swipe_entry["y2"] -= 10
        elif key == "down": swipe_entry["y2"] += 10
        elif key in ("+", "="): 
            swipe_entry["duration_ms"] += 100
        elif key in ("-", "\u2212", "minus"):
            swipe_entry["duration_ms"] = max(50, swipe_entry["duration_ms"] - 100)
        elif key == "r":
            run_adb_swipe(
                swipe_entry["x1"], swipe_entry["y1"],
                swipe_entry["x2"], swipe_entry["y2"],
                swipe_entry["duration_ms"]
            )
        elif key == "s":
            return swipe_entry  # save and exit
        elif key == "b":
            print("[INFO] Going back to gesture list.")
            return None  # sentinel for back
        elif key == "q":
            print("[INFO] Discarding changes and exiting.")
            exit()

def run_tap(name):
    print(f"\nReady to tap: {name}")
    print("[r] Replay | [b] Back to gesture list | [q] Quit")
    while True:
        key = keyboard.read_event()
        if key.event_type != keyboard.KEY_DOWN:
            continue
        k = key.name
        if k == "r":
            tap_now(name)
        elif k == "b":
            return
        elif k == "q":
            exit()

def print_controls():
    print("[←/→]: Adjust x2 | [↑/↓]: Adjust y2")
    print("[+/-]: Adjust duration (ms)")
    print("[r]: Replay gesture")
    print("[s]: Save and exit")
    print("[q]: Quit without saving\n")

def main():
    clickmap = load_clickmap()

    while True:
        name, entry = choose_gesture(clickmap)
        if "swipe" in entry:
            updated = edit_swipe(name, entry["swipe"].copy())
            if updated is not None:
                entry["swipe"] = updated
                clickmap[name] = entry
                save_clickmap(clickmap)
        elif "tap" in entry:
            run_tap(name)

if __name__ == "__main__":
    main()
