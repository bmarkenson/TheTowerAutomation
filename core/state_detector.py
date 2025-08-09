# core/state_detector.py

from utils.template_matcher import match_region
from utils.logger import log
from core.clickmap_access import get_clickmap, resolve_dot_path
import yaml
import os

STATE_DEF_PATH = os.path.join(os.path.dirname(__file__), "../config/state_definitions.yaml")

def load_state_definitions():
    with open(STATE_DEF_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

state_definitions = load_state_definitions()
clickmap = get_clickmap()

def detect_state_and_overlays(screen):
    result = {
        "state": "UNKNOWN",
        "secondary_states": [],
        "overlays": []
    }

    matched_states = []

    # Match all states
    for state in state_definitions.get("states", []):
        state_name = state["name"]
        match_keys = state.get("match_keys", [])
        for key in match_keys:
            entry = resolve_dot_path(key)
            if not entry:
                continue
            pt, conf = match_region(screen, entry)
            if pt:
                log(f"[MATCH] State {state_name} via {key} at {pt} ({conf:.3f})", "MATCH")
                matched_states.append(state_name)
                break

    # Classify into primary and secondary
    for name in matched_states:
        state_entry = next((s for s in state_definitions["states"] if s["name"] == name), None)
        if not state_entry:
            continue
        state_type = state_entry.get("type", "unknown")

        if state_type == "primary":
            if result["state"] != "UNKNOWN":
                raise RuntimeError(f"[ERROR] Multiple primary states matched: {result['state']} and {name}")
            result["state"] = name
        else:
            result["secondary_states"].append(name)

    # Match overlays (can be multiple)
    for overlay in state_definitions.get("overlays", []):
        overlay_name = overlay["name"]
        for key in overlay.get("match_keys", []):
            entry = resolve_dot_path(key)
            if not entry:
                log(f"[WARN]     Could not resolve: {key}", "WARN")
                continue
            pt, conf = match_region(screen, entry)
            if pt:
                log(f"[MATCH] Overlay {overlay_name} via {key} at {pt} ({conf:.3f})", "MATCH")
                result["overlays"].append(overlay_name)
                break

    return result
