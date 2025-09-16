from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
import yaml

from utils.logger import log
from .base import BaseMission, MissionContext
from core.floating_button_detector import detect_floating_buttons


def _elapsed_secs(ctx: MissionContext) -> float:
    return max(0.0, time.time() - (ctx.run_started_ts or time.time()))


def _cmp_elapsed(cond: Any, ctx: MissionContext) -> bool:
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
        # Default: threshold
        return el >= float(s)
    except Exception:
        return False


def _bool_assert(expr: Any, vars: Dict[str, Any]) -> bool:
    # Minimal: support bare var truthiness and negation !var, and var == value
    try:
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
            # try int/float compare, else string
            try:
                return float(val) == float(v)
            except Exception:
                return str(val) == v
        return bool(vars.get(s))
    except Exception:
        return False


class YamlMission(BaseMission):
    name = "yaml"

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.vars: Dict[str, Any] = dict(self.config.get("vars") or {})
        self.per_run_reset: List[str] = list(self.config.get("per_run_reset") or [])
        self.rules: List[Dict[str, Any]] = list(self.config.get("rules") or [])

    @classmethod
    def from_file(cls, path: str) -> "YamlMission":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def on_start(self, ctx: MissionContext) -> None:
        # Initialize variables from config on process start
        ctx.data.setdefault("mission_vars", {})
        ctx.data["mission_vars"].update(self.vars)

    def on_run_start(self, ctx: MissionContext) -> None:
        super().on_run_start(ctx)
        mv = ctx.data.setdefault("mission_vars", {})
        for k in self.per_run_reset:
            mv[k] = False if isinstance(mv.get(k), bool) else 0

    def _floating_visible(self, screen, name: str) -> bool:
        try:
            btns = detect_floating_buttons(screen)
            return any(b.get("name") == name for b in btns)
        except Exception:
            return False

    def _cond_ok(self, ctx: MissionContext, screen, detection: Dict[str, Any], conds: Dict[str, Any]) -> bool:
        mv = ctx.data.get("mission_vars", {})
        # state
        st_req = conds.get("state")
        if st_req and detection.get("state") != st_req:
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
        # floating visible
        fv = conds.get("floating_visible")
        if fv and not self._floating_visible(screen, fv):
            return False
        # assert
        assertion = conds.get("assert")
        if assertion is not None and not _bool_assert(assertion, mv):
            return False
        return True

    def _apply_set(self, ctx: MissionContext, act: Dict[str, Any]) -> None:
        mv = ctx.data.setdefault("mission_vars", {})
        var = act.get("var")
        if var:
            mv[var] = act.get("value")

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]):
        actions: List[Dict[str, Any]] = []
        for rule in self.rules:
            when = rule.get("when") or {}
            if not self._cond_ok(ctx, screen, detection, when):
                continue
            for act in (rule.get("do") or []):
                t = (act or {}).get("type")
                if t == "set":
                    self._apply_set(ctx, act)
                else:
                    actions.append(act)
            # Minimal evaluator: fire first matching rule per tick
            if actions:
                log(f"[MISSION yaml] Rule fired: {when}", "DEBUG")
                break
        return actions or None
