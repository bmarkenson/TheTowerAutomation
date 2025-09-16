#!/usr/bin/env python3
"""
core/action_executor.py

Thin executor for strategy-emitted actions. Keeps tap authority centralized and
respects current pause gates by letting the caller decide when to invoke it.

Action schema (dict-based for simplicity at start):
  {"type": "tap_label", "key": "buttons.retry:game_over"}
  {"type": "sleep", "ms": 300}
  {"type": "fire_floating", "name": "floating_buttons.nuke"}

Future: extend with swipe/page actions or convert to dataclasses.
"""

from __future__ import annotations

from typing import Iterable, Dict, Any
from utils.logger import log
from core.label_tapper import tap_label_now
from core.floating_button_detector import detect_floating_buttons, tap_floating_button
from core.run_controls import restart_run


def execute_actions(screen, actions: Iterable[Dict[str, Any]]) -> None:
    for act in actions or []:
        try:
            t = (act or {}).get("type")
            if t == "tap_label":
                key = act.get("key")
                if key:
                    tap_label_now(key)
            elif t == "restart_run":
                restart_run()
            elif t == "fire_floating":
                name = act.get("name")
                if name:
                    buttons = detect_floating_buttons(screen)
                    if not tap_floating_button(name, buttons):
                        log(f"[EXEC] Floating button not present: {name}", "DEBUG")
            elif t == "sleep":
                import time
                ms = int(act.get("ms", 0))
                time.sleep(max(0, ms) / 1000.0)
            else:
                log(f"[EXEC] Unknown action: {act}", "WARN")
        except Exception as e:
            log(f"[EXEC] Exception during action {act}: {e}", "ERROR")
