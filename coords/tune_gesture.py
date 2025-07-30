#!/usr/bin/env python3

import os
import time
import json
import subprocess
import keyboard

CLICKMAP_PATH = os.path.join(os.path.dirname(__file__), "clickmap.json")

def load_clickmap():
    with open(CLICKMAP_PATH, "r") as f:
        return json.load(f)

def save_clickmap(data):
    tmp = CLICKMAP_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CLICKMAP_PATH)
    print("[INFO] Saved changes.")

def run_adb_tap(x, y):
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)])

def run_adb_swipe(x1, y1, x2, y2, duration):
    subprocess.run([
        "adb", "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2), str(duration)
    ])

def choose_gesture(clickmap):
    print("Available gestures:")
    entries = list(clickmap.items())
    for idx, (key, val) in enumerate(entries, 1):
        gtype = "swipe" if "x1" in val else "tap"
        print(f"[{idx}] {key:20} ({gtype})")
    while True:
        try:
            i = int(input("Enter gesture number: ")) - 1
            if 0 <= i < len(entries):
                return entries[i][0], entries[i][1]
        except ValueError:
            continue

def edit_swipe(name, entry, original):
    print(f"\nEditing: {name}")
    print_controls()
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"\nEditing: {name}")
        print(f"Start:   (x1={entry['x1']}, y1={entry['y1']})")
        print(f"End:     (x2={entry['x2']}, y2={entry['y2']})")
        print(f"Duration: {entry['duration_ms']} ms\n")
        print_controls()

        event = keyboard.read_event()

        if event.event_type != keyboard.KEY_DOWN:
            continue
        key = event.name


        if key == "left": entry["x2"] -= 10
        elif key == "right": entry["x2"] += 10
        elif key == "up": entry["y2"] -= 10
        elif key == "down": entry["y2"] += 10
        elif key in ("+", "="): 
            entry["duration_ms"] += 100
        elif key in ("-", "\u2212", "minus"):
            entry["duration_ms"] = max(50, entry["duration_ms"] - 100)
        elif key == "r": run_adb_swipe(entry["x1"], entry["y1"], entry["x2"], entry["y2"], entry["duration_ms"])
        elif key == "s":
            return entry  # save and exit
        elif key == "b":
            print("[INFO] Going back to gesture list.")
            return None  # sentinel for back
        elif key == "q":
            print("[INFO] Discarding changes and exiting.")
            exit()


def run_tap(name, entry):
    print(f"\nRunning: {name} (x={entry['x']}, y={entry['y']})")
    print("[r] Replay | [b] Back to gesture list | [q] Quit")
    while True:
        key = keyboard.read_event()
        if key.event_type != keyboard.KEY_DOWN:
            continue
        k = key.name
        if k == "r":
            run_adb_tap(entry["x"], entry["y"])
        elif k == "b":
            return  # go back to main menu
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
        original = entry.copy()
    
        if "x1" in entry:
            updated = edit_swipe(name, entry.copy(), original)
            if updated is not None:
                clickmap[name] = updated
                save_clickmap(clickmap)
        elif "x" in entry:
            run_tap(name, entry)


if __name__ == "__main__":
    main()
