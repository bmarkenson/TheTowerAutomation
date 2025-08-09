# core/detector_overlay_support.py

from utils import template_matcher as tm
from core.clickmap_access import resolve_dot_path
from utils.logger import log


def _resolve_region_from_entry(entry):
    """
    Return a dict with x,y,w,h for the region to scan.
    Prefers inline match_region; falls back to region_ref which can be either
    a bare name under _shared_match_regions or a fully-qualified dot path.
    """
    region = entry.get("match_region")
    if region:
        return region

    ref = entry.get("region_ref")
    if not ref:
        return None

    shared_entry = (
        resolve_dot_path(f"_shared_match_regions.{ref}") or
        resolve_dot_path(ref)
    )
    if shared_entry and isinstance(shared_entry, dict):
        return shared_entry.get("match_region")

    return None


def detect_overlay_via_detector(overlay_name, overlay_def, screen):
    """
    Attempts to resolve and run a detector function defined via a detector overlay entry.
    Returns True if match succeeded, False otherwise.
    """
    keys = overlay_def.get("match_keys", [])
    if not keys:
        log(f"[WARN] Detector overlay '{overlay_name}' missing match_keys.", "WARN")
        return False

    if len(keys) != 1:
        log(f"[WARN] Detector overlay '{overlay_name}' should have exactly one match_key; got {len(keys)}.", "WARN")

    entry = resolve_dot_path(keys[0])
    if not entry:
        log(f"[WARN] Could not resolve detector entry: {keys[0]}", "WARN")
        return False

    detector_name = entry.get("detector")
    if not detector_name:
        log(f"[WARN] Detector overlay '{overlay_name}' has no 'detector' in clickmap entry {keys[0]}.", "WARN")
        return False

    detector_fn = getattr(tm, detector_name, None)
    if not callable(detector_fn):
        log(f"[ERROR] Detector function '{detector_name}' not found in utils/template_matcher.py", "ERROR")
        return False

    region = _resolve_region_from_entry(entry)
    if not region:
        log(f"[WARN] Detector overlay '{overlay_name}' has no resolvable region.", "WARN")
        return False

    try:
        detected = detector_fn(screen, region, debug=False)
    except Exception as e:
        log(f"[ERROR] Detector '{detector_name}' for '{overlay_name}' raised: {e}", "ERROR")
        detected = False

    if detected:
        log(f"[MATCH] Overlay {overlay_name} via detector {detector_name}", "MATCH")
        return True

    return False


