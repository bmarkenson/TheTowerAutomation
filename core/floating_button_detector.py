import os
import cv2
from utils.template_matcher import match_region
from core.clickmap_access import resolve_dot_path, get_clickmap, get_entries_by_role
from utils.logger import log
from core.adb_utils import adb_shell

def tap_floating_button(name, buttons):
    for b in buttons:
        if b["name"] == name:
            x, y = b["tap_point"]["x"], b["tap_point"]["y"]
            log(f"TAP_FLOATING: {name} at ({x},{y})", "ACTION")
            adb_shell(["input", "tap", str(x), str(y)])
            return True
    return False

def detect_floating_buttons(screen):
    results = []
    floating_buttons = get_entries_by_role("floating_button")
    #print(f"[DEBUG] Found floating button entries: {list(floating_buttons.keys())}")

    for name, entry in floating_buttons.items():
        try:
            if not entry:
                continue
    
            pt, conf = match_region(screen, entry)
            if pt is None:
                print(f"[DEBUG] {name} not matched (conf={conf:.2f})")
        
            else:
                #print(f"[DEBUG] {name} matched (conf={conf:.2f}) at {pt}")
        
                template_path = entry["match_template"]
                template = cv2.imread(os.path.join("assets/match_templates", template_path))

                if not os.path.exists(os.path.join("assets/match_templates", template_path)):
                    print(f"[ERROR] Template file missing: {template_path}")
        
                h, w = template.shape[:2]
        
                x, y = pt
                results.append({
                    "name": name,
                    "match_region": {"x": x, "y": y, "w": w, "h": h},
                    "confidence": conf,
                    "tap_point": {"x": x + w // 2, "y": y + h // 2}
                })
        except Exception as e:
            print(f"[ERROR] Exception during processing of {name}: {e}")

    return results
