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
from typing import Any, Dict, List, Optional, TypedDict

import numpy as np
import yaml

from utils.template_matcher import match_region
from utils.logger import log
from core.clickmap_access import get_clickmap, resolve_dot_path
from core.matcher import get_match

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
        - If no primary matches, state remains "UNKNOWN"
    """
    definitions = state_defs or state_definitions
    states_config: List[Dict[str, Any]] = definitions.get("states", [])
    overlays_config: List[Dict[str, Any]] = definitions.get("overlays", [])

    state_lookup = {entry.get("name"): entry for entry in states_config if entry.get("name")}

    result: StateDetectionResult = {
        "state": "UNKNOWN",
        "secondary_states": [],
        "overlays": [],
        "menu": None,  # mutually-exclusive menu secondary (from states with type: menu)
    }

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

    # Classify into primary, secondary, and menu (mutually exclusive selection)
    menu_candidates_in_order = []  # preserve YAML order for priority
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
        elif state_type == "menu":
            menu_candidates_in_order.append(name)
        else:
            result["secondary_states"].append(name)

    if menu_candidates_in_order:
        # pick the first matched in YAML order (order = priority)
        result["menu"] = menu_candidates_in_order[0]
        if len(menu_candidates_in_order) > 1:
            log(f"[WARN] Multiple menus matched {menu_candidates_in_order} -> chose '{result['menu']}' (YAML order priority)", "WARN")

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
