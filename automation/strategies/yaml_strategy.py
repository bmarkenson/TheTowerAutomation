from __future__ import annotations

"""
YAML-driven strategy evaluator (same schema as YamlMission).

Evaluates rules each tick while RUNNING and emits executor actions.
Supports per-rule cooldown (cooldown_sec) and a small set of conditions.
"""

import copy
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
        session_preflight = self.config.get("session_preflight") or {}
        self._session_preflight_assertions: List[Any] = list(
            session_preflight.get("complete_when") or []
        )
        self._session_preflight_requirements: Dict[str, Any] = dict(
            session_preflight.get("requirements") or {}
        )
        self._session_preflight_gate_fallbacks: Dict[str, Any] = copy.deepcopy(
            session_preflight.get("fallbacks") or {}
        )
        self._run_configuration: Dict[str, Any] = copy.deepcopy(
            self.config.get("run_configuration") or {}
        )
        self._runtime_policy: Dict[str, Any] = copy.deepcopy(
            self.config.get("runtime_policy") or {}
        )
        attached_validation_action: Optional[Dict[str, Any]] = None
        if (
            self._runtime_policy.get("session_preflight_on_attach") is True
        ):
            for rule in self.rules:
                if str(rule.get("gate_phase") or "") != "session_preflight":
                    continue
                for action in rule.get("do") or []:
                    if (action or {}).get("type") in {
                        "gc_session_preflight",
                        "session_preflight",
                    }:
                        attached_validation_action = copy.deepcopy(action)
                        break
                if attached_validation_action is not None:
                    break
        elif self._session_preflight_requirements:
            attached_validation_action = {
                "type": "gc_session_preflight",
                "requirements": copy.deepcopy(
                    self._session_preflight_requirements
                ),
                "mismatch_policy": "block",
            }
        if self._session_preflight_assertions and attached_validation_action:
            attached_validation_action["allow_repair"] = False
            self.rules.insert(
                0,
                {
                    "name": "validate_requested_attached_session_preflight",
                    "gate_phase": "session_preflight",
                    "run_when_attached": True,
                    "attached_validation_only": True,
                    "when": {"state": "RUNNING"},
                    "assert": [
                        "attached_validation_requested",
                        "!gc_session_preflight_completed",
                        "!gc_session_preflight_attempted",
                    ],
                    "cooldown_sec": 30.0,
                    "do": [attached_validation_action],
                }
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
            if k in self.vars:
                mv[k] = copy.deepcopy(self.vars[k])
            else:
                mv[k] = False if isinstance(mv.get(k), bool) else 0

    def requires_run_initialization(self) -> bool:
        return bool(self._run_initialization_assertions)

    def is_run_initialization_complete(self, ctx) -> bool:
        if not self.requires_run_initialization():
            return True
        mv = ctx.data.get("mission_vars", {})
        return _bool_assert(self._run_initialization_assertions, mv)

    def requires_session_preflight(self) -> bool:
        return bool(self._session_preflight_assertions)

    def is_session_preflight_complete(self, ctx) -> bool:
        if not self.requires_session_preflight():
            return True
        mv = ctx.data.get("mission_vars", {})
        return _bool_assert(self._session_preflight_assertions, mv)

    def session_preflight_requirements(self) -> Dict[str, Any]:
        return dict(self._session_preflight_requirements)

    def session_preflight_gate_fallbacks(self) -> Dict[str, Any]:
        return copy.deepcopy(self._session_preflight_gate_fallbacks)

    def _session_preflight_fingerprint_payload(self) -> Dict[str, Any]:
        """Include every generated session-gate rule in the reuse identity."""

        return {
            "strategy": self.name,
            "session_preflight": {
                "complete_when": copy.deepcopy(
                    self._session_preflight_assertions
                ),
                "requirements": copy.deepcopy(
                    self._session_preflight_requirements
                ),
                "fallbacks": copy.deepcopy(
                    self._session_preflight_gate_fallbacks
                ),
                "rules": [
                    copy.deepcopy(rule)
                    for rule in self.rules
                    if str(rule.get("gate_phase") or "").strip()
                    == "session_preflight"
                ],
            },
            "runtime_policy": {
                "session_preflight_on_attach": self._runtime_policy.get(
                    "session_preflight_on_attach"
                )
            },
        }

    def _definition_fingerprint_payload(self) -> Dict[str, Any]:
        """Bind attachment semantics to the complete generated YAML plan."""

        return copy.deepcopy(self.config)

    def run_configuration(self) -> Dict[str, Any]:
        return copy.deepcopy(self._run_configuration)

    def runtime_policy(self) -> Dict[str, Any]:
        return copy.deepcopy(self._runtime_policy)

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
        mv = ctx.data.setdefault("mission_vars", {})

        for idx, rule in enumerate(self.rules):
            rid = str(rule.get("name") or idx)
            gate_phase = str(rule.get("gate_phase") or "").strip()
            attachment_validation_rule = bool(
                ctx.data.get("startup_gates_deferred")
                and gate_phase in {"run_initialization", "session_preflight"}
                and bool(rule.get("run_when_attached"))
            )
            if attachment_validation_rule:
                dispositions = mv.get("attached_validation_rule_dispositions")
                if isinstance(dispositions, dict) and rid in dispositions:
                    continue
            if ctx.data.get("startup_gates_deferred") and gate_phase in {
                "run_initialization",
                "session_preflight",
            }:
                if (
                    ctx.data.get("skip_attached_checks")
                    or ctx.data.get("attached_session_preflight_reused")
                ):
                    continue
                if (
                    ctx.data.get("attached_validation_requested")
                    and not mv.get("gc_session_preflight_attempted")
                ):
                    # Give the attached inventory pass exclusive authority
                    # until it reaches a conclusive result.  After that pass,
                    # continue with any explicitly declared battle-only
                    # attachment rules instead of stranding their completion
                    # assertions behind the attached-only filter.
                    if not bool(rule.get("attached_validation_only")):
                        continue
                if not bool(rule.get("run_when_attached")):
                    continue
            when = dict(rule.get("when") or {})
            if "assert" in rule:
                when["assert"] = rule["assert"]
            if not self._cond_ok(ctx, screen, detection, when):
                continue

            # cooldown gate
            cd = float(rule.get("cooldown_sec") or 0.0)
            now = time.time()
            last_ts = float(last_fire.get(rid) or 0.0)
            if cd > 0 and (now - last_ts) < cd:
                continue

            for act in (rule.get("do") or []):
                t = (act or {}).get("type")
                if t == "set":
                    var = act.get("var")
                    if var:
                        mv[var] = act.get("value")
                else:
                    materialized = copy.deepcopy(act)
                    if attachment_validation_rule and isinstance(
                        materialized,
                        dict,
                    ):
                        materialized["_attachment_validation"] = True
                        materialized["_attachment_rule_id"] = rid
                    actions.append(materialized)

            if actions:
                log_mission(f"[YAML] Rule fired: {rid}", "DEBUG")
                last_fire[rid] = now
                break

        return actions or None


__all__ = ["YamlStrategy"]
