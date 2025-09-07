# quick_match_probe.py
import cv2
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.matcher import _match_entry

get_clickmap()  # ensure cache is warm

screen = cv2.imread("screenshots/latest.png")
entry = resolve_dot_path("indicators.game_over")
print("Resolved?", bool(entry))
if entry:
    print("Entry:", {k: entry[k] for k in ("match_template","match_region","match_threshold") if k in entry})
    (pt, conf) = _match_entry(screen, entry, template_dir="assets/match_templates")
    print("Result:", pt, conf)


