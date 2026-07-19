# core/state_detector.py
"""
State detection against clickmap + YAML rules.

This module reads `config/state_definitions.yaml` and classifies a frame into a
single **primary** state, optional **secondary** states, optional mutually
exclusive **menu** state, and any number of **overlays**.

YAML-in-docstring legend (kept tiny and consistent per module)

spec_legend:
  r: Return value (shape & invariants)
  s: Side effects (tags from project primer)
  e: Errors/exceptions behavior
  p: Parameters (only non-obvious notes; types are in signature)
  notes: Brief extra context that aids correct use

defaults:
  threshold_default: 0.90
  images: BGR; origin=(0,0) top-left
  matcher: OpenCV TM_CCOEFF_NORMED via utils.template_matcher/core.matcher
  clickmap: config/clickmap.json (resolved via core.clickmap_access)
  state_yaml: config/state_definitions.yaml (safe_load)
  invariants:
    - Exactly one primary state per frame; multiple → RuntimeError
    - Menus are mutually exclusive; choose first match in YAML order
    - Overlays: 0..N may co-exist
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import numpy as np
import yaml
from PIL import Image

from utils.template_matcher import match_region
from utils.logger import log
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.matcher import get_match
from core.ss_capture import is_complete_screenshot

STATE_DEF_PATH = os.path.join(os.path.dirname(__file__), "../config/state_definitions.yaml")


class StateDetectionResult(TypedDict):
    """Typed structure returned by :func:`detect_state_and_overlays`."""

    state: str
    secondary_states: List[str]
    overlays: List[str]
    menu: Optional[str]


def load_state_definitions(path: str = STATE_DEF_PATH) -> Dict[str, Any]:
    """
    spec:
      name: load_state_definitions
      signature: load_state_definitions() -> dict
      r: YAML dict loaded via yaml.safe_load from STATE_DEF_PATH
      s: [fs]
      e:
        - FileNotFoundError: when the YAML file path is missing
        - yaml.YAMLError: when parsing fails
      notes:
        - Caller treats the structure as authoritative for state/menu/overlay rules
    """
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


state_definitions: Dict[str, Any] = load_state_definitions()
get_clickmap()

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "match_templates"


def reload_state_definitions(path: str = STATE_DEF_PATH) -> Dict[str, Any]:
    """Reload state definitions from disk and clear cached clickmap lookups."""

    global state_definitions
    state_definitions = load_state_definitions(path)
    _resolve_dot_path_cached.cache_clear()
    return state_definitions


@lru_cache(maxsize=256)
def _resolve_dot_path_cached(dot_path: str) -> Optional[Dict[str, Any]]:
    """Memoized access to clickmap entries to avoid repeated lookups per frame."""

    return resolve_dot_path(dot_path)


def detect_state_and_overlays(
    screen: np.ndarray,
    *,
    log_matches: bool = False,
    state_defs: Optional[Dict[str, Any]] = None,
) -> StateDetectionResult:
    """
    spec:
      name: detect_state_and_overlays
      signature: detect_state_and_overlays(screen, *, log_matches: bool = False, state_defs: Optional[dict] = None) -> dict
      p:
        screen: BGR ndarray (full screen capture)
        log_matches: emit MATCH logs for debugging if True
        state_defs: optional pre-loaded definitions, useful for tests
      r:
        dict with keys:
          state: str  # one of primary names or "UNKNOWN"
          secondary_states: list[str]
          overlays: list[str]
          menu: str|null  # chosen from states of type: menu by YAML order
      s: [cv2][state][log]
      e:
        - RuntimeError: when multiple primary states match in the same frame
      notes:
        - Uses core.matcher.get_match for primary/menu checks (clickmap-backed)
        - Uses utils.template_matcher.match_region for overlay checks
        - Unresolved clickmap keys are WARN-logged and skipped
        - Incomplete frames return UNKNOWN without running template matches
        - If no primary matches, state remains "UNKNOWN"
    """
    result: StateDetectionResult = {
        "state": "UNKNOWN",
        "secondary_states": [],
        "overlays": [],
        "menu": None,  # mutually-exclusive menu secondary (from states with type: menu)
    }
    if not is_complete_screenshot(screen):
        log("[STATE] Incomplete screenshot rejected before detection", "WARN")
        return result

    definitions = state_defs or state_definitions
    states_config: List[Dict[str, Any]] = definitions.get("states", [])
    overlays_config: List[Dict[str, Any]] = definitions.get("overlays", [])

    state_lookup = {entry.get("name"): entry for entry in states_config if entry.get("name")}

    matched_states = []

    # Match all states
    for state in states_config:
        state_name = state.get("name")
        if not state_name:
            continue
        match_keys = state.get("match_keys", [])
        for key in match_keys:
            entry = _resolve_dot_path_cached(key)
            if not entry:
                log(f"[WARN] Unresolved key: {key}", "WARN")
                continue
            if "match_template" not in entry:
                log(f"[WARN] No match_template for {key}; template matcher will always fail", "WARN")
                continue
            pt, conf = get_match(key, screenshot=screen)
            if pt:
                if log_matches:
                    log(f"[MATCH] State {state_name} via {key} at {pt} ({conf:.3f})", "MATCH")
                matched_states.append(state_name)
                break

    # Classify into primary, secondary, menu, and fallback-primary candidates.
    # Fallback primaries describe modal screens whose evidence may coexist with
    # a more specific primary (for example, an upgrade detail shown over the
    # dedicated Damage Adjuster). They are authoritative only when no ordinary
    # primary matched.
    menu_candidates_in_order = []  # preserve YAML order for priority
    fallback_primary_candidates = []
    for name in matched_states:
        # find the state entry (by name) in YAML
        state_entry = state_lookup.get(name)
        if not state_entry:
            continue
        state_type = state_entry.get("type", "unknown")

        if state_type == "primary":
            if result["state"] != "UNKNOWN":
                raise RuntimeError(f"[ERROR] Multiple primary states matched: {result['state']} and {name}")
            result["state"] = name
        elif state_type == "fallback_primary":
            fallback_primary_candidates.append(name)
        elif state_type == "menu":
            menu_candidates_in_order.append(name)
        else:
            result["secondary_states"].append(name)

    if result["state"] == "UNKNOWN" and fallback_primary_candidates:
        result["state"] = fallback_primary_candidates[0]
        if len(fallback_primary_candidates) > 1:
            log(
                f"[WARN] Multiple fallback primaries matched "
                f"{fallback_primary_candidates} -> chose "
                f"'{result['state']}' (YAML order priority)",
                "WARN",
            )

    if menu_candidates_in_order:
        # pick the first matched in YAML order (order = priority)
        result["menu"] = menu_candidates_in_order[0]
        if len(menu_candidates_in_order) > 1:
            log(f"[WARN] Multiple menus matched {menu_candidates_in_order} -> chose '{result['menu']}' (YAML order priority)", "WARN")

    result["secondary_states"] = _resolve_card_secondary_conflicts(
        screen,
        result["secondary_states"],
        state_lookup,
    )

    # Match overlays (can be multiple)
    for overlay in overlays_config:
        overlay_name = overlay.get("name")
        if not overlay_name:
            continue
        for key in overlay.get("match_keys", []):
            entry = _resolve_dot_path_cached(key)
            if not entry:
                log(f"[WARN]     Could not resolve: {key}", "WARN")
                continue
            pt, conf = match_region(screen, entry)
            if pt:
                if log_matches:
                    log(f"[MATCH] Overlay {overlay_name} via {key} at {pt} ({conf:.3f})", "MATCH")
                result["overlays"].append(overlay_name)
                break

    return result


def _resolve_card_secondary_conflicts(
    screen: np.ndarray,
    secondary_states: List[str],
    state_lookup: Dict[str, Any],
) -> List[str]:
    cards_states = [
        "CARDS_GCFARM_EARLY",
        "CARDS_GCFARM_LATE",
    ]
    active = [name for name in secondary_states if name in cards_states]
    if len(active) <= 1:
        return secondary_states

    brightness_scores: Dict[str, float] = {}
    for state_name in active:
        entry = state_lookup.get(state_name) or {}
        key = (entry.get("match_keys") or [None])[0]
        region_entry = _resolve_dot_path_cached(key) if key else None
        if not region_entry:
            continue
        region = region_entry.get("match_region") or {}
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        roi = screen[y : y + h, x : x + w]
        if roi.size == 0:
            continue
        template_path = region_entry.get("match_template")
        mask = _load_template_mask(template_path) if template_path else None
        mask = _align_mask(mask, roi)
        brightness_scores[state_name] = _masked_brightness(roi, mask)

    if not brightness_scores:
        return secondary_states

    winner = max(brightness_scores, key=brightness_scores.get)
    filtered = [state for state in secondary_states if state == winner or state not in brightness_scores]
    return filtered


def _masked_brightness(roi: np.ndarray, mask: Optional[np.ndarray]) -> float:
    luminance = roi.astype(np.float32).mean(axis=2)
    if mask is not None and mask.shape == luminance.shape:
        values = luminance[mask]
    else:
        values = luminance.reshape(-1)
    if values.size == 0:
        return -1.0
    return float(values.mean())


def _align_mask(mask: Optional[np.ndarray], roi: np.ndarray) -> Optional[np.ndarray]:
    if mask is None:
        return None
    if mask.shape == roi.shape[:2]:
        return mask
    h, w = roi.shape[:2]
    mh, mw = mask.shape
    return mask[: min(mh, h), : min(mw, w)]


@lru_cache(maxsize=16)
def _load_template_mask(template_path: str) -> Optional[np.ndarray]:
    if not template_path:
        return None
    full_path = (ASSETS_ROOT / template_path).resolve()
    if not full_path.exists():
        return None
    try:
        img = Image.open(full_path).convert("L")
    except Exception:
        return None
    mask = np.array(img) > 0
    return mask
