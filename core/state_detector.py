# core/state_detector.py

import json
import os
from utils.template_matcher import match_region
from utils.logger import log

CLICKMAP_FILE = "coords/clickmap.json"

# Define which keys in the clickmap should be checked for state detection
STATE_MATCH_KEYS = [
    "game_over",
    "resume_game",
    "resume_battle",
    "Battle"
]

# Keys that indicate active gameplay screen (RUNNING state)
RUNNING_MATCH_KEYS = [
    "tower_ui",
    "upgrade_button"
    # Add more keys here as needed
]

# Mapping match keys to semantic states
STATE_MAP = {
    "game_over": "GAME_OVER",
    "resume_game": "RESUME_GAME",
    "resume_battle": "RESUME_BATTLE",
    "Battle": "HOME_SCREEN"
}

def load_clickmap():
    if os.path.exists(CLICKMAP_FILE):
        try:
            with open(CLICKMAP_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log(f"[ERROR] Failed to load clickmap: {e}", level="FAIL")
    return {}

def detect_state(screen):
    clickmap = load_clickmap()

    # Check for known priority screen states
    for key in STATE_MATCH_KEYS:
        entry = clickmap.get(key)
        if not entry:
            continue

        pt, conf = match_region(screen, entry)
        if pt:
            log(f"[MATCH] {key} matched at {pt} with confidence {conf:.3f}", "MATCH")
            return STATE_MAP.get(key, key.upper())

    # Check for signs that game is actively running
    for key in RUNNING_MATCH_KEYS:
        entry = clickmap.get(key)
        if not entry:
            continue
        pt, conf = match_region(screen, entry)
        if pt:
            log(f"[MATCH] {key} indicates active gameplay", "DEBUG")
            return "RUNNING"

    return "UNKNOWN"


