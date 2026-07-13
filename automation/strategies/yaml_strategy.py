from __future__ import annotations

"""
YAML-driven strategy evaluator (same schema as YamlMission).

Evaluates rules each tick while RUNNING and emits executor actions.
Supports per-rule cooldown (cooldown_sec) and a small set of conditions.
"""

import time
from typing import Any, Dict, List, Optional

import yaml

from automation.strategies.base import BaseStrategy
from core.floating_button_detector import detect_floating_buttons
from utils.logger import log_mission
from core.upgrade_box_detector import detect_visible_boxes


def _elapsed_secs(ctx) -> float:
    try:
        return max(0.0, time.time() - (ctx.run_started_ts or time.time()))
    except Exception:
        return 0.0


def _cmp_elapsed(cond: Any, ctx) -> bool:
    try:
        el = _elapsed_secs(ctx)
        if isinstance(cond, (int, float)):
            return el >= float(cond)
        s = str(cond).strip()
        if s.startswith(">="):
            return el >= float(s[2:].strip())
        if s.startswith(">"):
            return el > float(s[1:].strip())
        if s.startswith("<="):
            return el <= float(s[2:].strip())
        if s.startswith("<"):
            return el < float(s[1:].strip())
        if s.startswith("=="):
            return el == float(s[2:].strip())
        return el >= float(s)
    except Exception:
        return False


def _bool_assert(expr: Any, vars: Dict[str, Any]) -> bool:
    try:
        if isinstance(expr, (list, tuple)):
            return all(_bool_assert(part, vars) for part in expr)
        if isinstance(expr, bool):
            return expr
        s = str(expr).strip()
        if s.startswith("!"):
            v = s[1:].strip()
            return not bool(vars.get(v))
        if "==" in s:
            k, v = s.split("==", 1)
            k = k.strip()
            v = v.strip()
            val = vars.get(k)
            try:
                return float(val) == float(v)
            except Exception:
                return str(val) == v
        return bool(vars.get(s))
    except Exception:
        return False


def _cmp_numeric(value: Any, cond: Any) -> bool:
    try:
        actual = float(value)
    except Exception:
        return False

    try:
        if isinstance(cond, (int, float)):
            return actual >= float(cond)
        s = str(cond).strip()
        if s.startswith(">="):
            return actual >= float(s[2:].strip())
        if s.startswith(">"):
            return actual > float(s[1:].strip())
        if s.startswith("<="):
            return actual <= float(s[2:].strip())
        if s.startswith("<"):
            return actual < float(s[1:].strip())
        if s.startswith("=="):
            return actual == float(s[2:].strip())
        return actual >= float(s)
    except Exception:
        return False


class YamlStrategy(BaseStrategy):
    name = "yaml"

    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config or {}
        self.name = str((self.config.get("meta") or {}).get("name") or "yaml")
        self.vars: Dict[str, Any] = dict(self.config.get("vars") or {})
        self.per_run_reset: List[str] = list(self.config.get("per_run_reset") or [])
        self.rules: List[Dict[str, Any]] = list(self.config.get("rules") or [])
        initialization = self.config.get("run_initialization") or {}
        self._run_initialization_assertions: List[Any] = list(
            initialization.get("complete_when") or []
        )

    @classmethod
    def from_file(cls, path: str) -> "YamlStrategy":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def on_start(self, ctx) -> None:
        ctx.data.setdefault("mission_vars", {})
        ctx.data["mission_vars"].update(self.vars)
        ctx.data.setdefault("rule_last_fire", {})

    def on_run_start(self, ctx) -> None:
        super().on_run_start(ctx)
        mv = ctx.data.setdefault("mission_vars", {})
        for k in self.per_run_reset:
            mv[k] = False if isinstance(mv.get(k), bool) else 0

    def requires_run_initialization(self) -> bool:
        return bool(self._run_initialization_assertions)

    def is_run_initialization_complete(self, ctx) -> bool:
        if not self.requires_run_initialization():
            return True
        mv = ctx.data.get("mission_vars", {})
        return _bool_assert(self._run_initialization_assertions, mv)

    # ---------------------------- conditions ---------------------------------
    def _floating_visible(self, screen, name: str) -> bool:
        try:
            btns = detect_floating_buttons(screen)
            return any(b.get("name") == name for b in btns)
        except Exception:
            return False

    def _upgrade_maxed(self, screen, *, menu: Optional[str], label: str) -> bool:
        try:
            boxes_by_col = detect_visible_boxes(screen, menu=menu)
            for col in ("left", "right"):
                for box in boxes_by_col.get(col, []) or []:
                    if not box.text:
                        continue
                    if box.text.lower() == label.strip().lower():
                        return (box.affordability or "").lower() == "maxed"
        except Exception:
            return False
        return False

    def _cond_ok(self, ctx, screen, detection: Dict[str, Any], conds: Dict[str, Any]) -> bool:
        mv = ctx.data.get("mission_vars", {})
        # state
        st_req = conds.get("state")
        if st_req and detection.get("state") != st_req:
            return False
        # menu exact match (if provided by detector)
        menu_req = conds.get("menu")
        if menu_req and (detection.get("menu") or None) != menu_req:
            return False
        # secondary states contains / not contains
        secondary_states = set(detection.get("secondary_states") or [])
        sec_inc = conds.get("secondary_contains") or []
        sec_exc = conds.get("secondary_not_contains") or []
        if any(name for name in sec_inc if name not in secondary_states):
            return False
        if any(name for name in sec_exc if name in secondary_states):
            return False
        # overlays contains / not contains
        overlays = set(detection.get("overlays") or [])
        inc = conds.get("overlays_contains") or []
        exc = conds.get("overlays_not_contains") or []
        if any(i for i in inc if i not in overlays):
            return False
        if any(i for i in exc if i in overlays):
            return False
        # elapsed
        if "elapsed_secs" in conds and not _cmp_elapsed(conds.get("elapsed_secs"), ctx):
            return False
        # wave threshold
        if "wave" in conds:
            wave_val = detection.get("wave")
            if not _cmp_numeric(wave_val, conds.get("wave")):
                return False
        # floating visible
        fv = conds.get("floating_visible")
        if fv and not self._floating_visible(screen, fv):
            return False
        # upgrade_maxed
        upm = conds.get("upgrade_maxed")
        if isinstance(upm, dict):
            if not self._upgrade_maxed(screen, menu=upm.get("menu"), label=str(upm.get("label", ""))):
                return False
        # assert
        assertion = conds.get("assert")
        if assertion is not None and not _bool_assert(assertion, mv):
            return False
        return True

    # ---------------------------- tick --------------------------------------
    def tick(self, ctx, screen, detection: Dict[str, Any]):
        actions: List[Dict[str, Any]] = []
        last_fire: Dict[str, float] = ctx.data.setdefault("rule_last_fire", {})

        for idx, rule in enumerate(self.rules):
            when = dict(rule.get("when") or {})
            if "assert" in rule:
                when["assert"] = rule["assert"]
            if not self._cond_ok(ctx, screen, detection, when):
                continue

            # cooldown gate
            cd = float(rule.get("cooldown_sec") or 0.0)
            rid = str(rule.get("name") or idx)
            now = time.time()
            last_ts = float(last_fire.get(rid) or 0.0)
            if cd > 0 and (now - last_ts) < cd:
                continue

            for act in (rule.get("do") or []):
                t = (act or {}).get("type")
                if t == "set":
                    mv = ctx.data.setdefault("mission_vars", {})
                    var = act.get("var")
                    if var:
                        mv[var] = act.get("value")
                else:
                    actions.append(act)

            if actions:
                log_mission(f"[YAML] Rule fired: {rid}", "DEBUG")
                last_fire[rid] = now
                break

        return actions or None


__all__ = ["YamlStrategy"]
