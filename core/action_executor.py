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

import json
import re
import time
from typing import Any, Dict, Iterable, Optional

from utils.logger import log, log_mission
from core.input import tap_if_visible
from core.floating_button_detector import detect_floating_buttons, tap_floating_button
from core.run_controls import restart_run
from core.upgrade_navigation import (
    apply_menu_buy_quantities,
    ensure_ultimate_state,
    ensure_ultimate_toggles_on,  # legacy alias
    find_upgrade,
)
from core.upgrade_buy_quantity import BuyQuantity
from core.target_priority import (
    ensure_target_priority_order,
    observe_target_priority_order,
)
from core.level_skip_initializer import initialize_level_skips
from core.damage_adjuster import configure_damage_slider
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    run_read_only_gc_preflight,
)
from core.tournament_preflight import (
    validate_tournament_session_preflight_screens,
)
from automation.missions.base import MissionContext
from handlers.ad_gem_handler import (
    is_blind_gem_tapper_active,
    start_blind_gem_tapper,
    stop_blind_gem_tapper,
)


Action = Dict[str, Any]


def execute_actions(screen, actions: Iterable[Action], ctx: Optional[MissionContext] = None) -> None:
    mv = None
    if ctx is not None:
        mv = ctx.data.setdefault("mission_vars", {})
    for act in actions or []:
        try:
            t = (act or {}).get("type")
            is_strategy_action = bool((act or {}).get("_strategy"))
            if is_strategy_action and isinstance(act, dict):
                act.pop("_strategy", None)

            last_state = mv.get("last_detection_state") if mv is not None else None

            state_guard = None
            if isinstance(act, dict):
                allowed = act.pop("_allow_states", None)
                if allowed is None:
                    allowed = act.get("state_guard")
                if allowed:
                    if isinstance(allowed, str):
                        state_guard = {allowed}
                    else:
                        state_guard = set(allowed)
            allowed_states = state_guard or {"RUNNING"}

            if t == "tap_label":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip tap_label while state={last_state}",
                        "DEBUG",
                    )
                    continue
                key = act.get("key")
                if key:
                    _maybe_suspend_blind_tapper_for_cards(key, mv)
                    tap_if_visible(key)
            elif t == "restart_run":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip restart_run while state={last_state}",
                        "DEBUG",
                    )
                    continue
                restart_run()
            elif t == "fire_floating":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip fire_floating while state={last_state}",
                        "DEBUG",
                    )
                    continue
                name = act.get("name")
                if name:
                    buttons = detect_floating_buttons(screen)
                    if not tap_floating_button(name, buttons):
                        log(f"[EXEC] Floating button not present: {name}", "DEBUG")
            elif t == "sleep":
                ms = int(act.get("ms", 0))
                _sleep_ms(ms)
            elif t == "upgrade_set_buy_quantities":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip upgrade_set_buy_quantities while state={last_state}",
                        "DEBUG",
                    )
                    continue
                menus = {}
                for k in ("attack", "defense", "utility"):
                    v = (act.get(k) or "").strip().lower()
                    if v:
                        if v in {"max", "x100", "x10", "x5", "x1"}:
                            menus[k] = v  # type: ignore[assignment]
                        else:
                            log_mission(f"[EXEC] Invalid buy quantity '{v}' for {k}", "WARN")
                if menus:
                    apply_menu_buy_quantities(menus)  # type: ignore[arg-type]
            elif t == "upgrade_purchase":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip upgrade_purchase while state={last_state}",
                        "DEBUG",
                    )
                    continue
                label = act.get("label")
                menu = act.get("menu")
                quantity: Optional[BuyQuantity] = None
                qv = (act.get("quantity") or "").strip().lower()
                if qv in {"max", "x100", "x10", "x5", "x1"}:
                    quantity = qv  # type: ignore[assignment]
                if not label or not menu:
                    log_mission(f"[EXEC] upgrade_purchase missing label/menu: {act}", "WARN")
                else:
                    res = find_upgrade(menu, label, attempt_purchase=True, purchase_quantity=quantity)
                    sent = bool(res and res.purchase_sent)
                    reason = (res.purchase_reason if res else None) or "unknown"
                    maxed_after = bool(res and res.post_purchase_maxed)
                    if reason.startswith("status=maxed"):
                        maxed_after = True
                    if mv is not None:
                        mv["last_upgrade_label"] = label
                        mv["last_upgrade_menu"] = menu
                        mv["last_upgrade_sent"] = sent
                        mv["last_upgrade_reason"] = reason
                        mv["last_upgrade_maxed_after"] = maxed_after
                        mv["last_upgrade_ts"] = time.time()
                        slug = _slugify_label(label)
                        if slug:
                            key = f"maxed_{slug}"
                            if reason == "status=maxed" or maxed_after:
                                mv[key] = True
                            elif sent or reason == "status=unaffordable":
                                mv[key] = False
                    log_level = "INFO"
                    if not sent and reason.startswith("status="):
                        log_level = "DEBUG"
                    log_mission(
                        (
                            f"[EXEC] upgrade_purchase label='{label}' menu='{menu}' "
                            f"sent={sent} reason={reason} maxed_after={maxed_after}"
                        ),
                        log_level,
                    )
            elif t == "level_skip_initialize":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip level_skip_initialize while state={last_state}",
                        "DEBUG",
                    )
                    continue
                result = initialize_level_skips(screenshot=screen)
                if mv is not None:
                    mv["ehls_completed"] = result.ehls_maxed
                    mv["eals_completed"] = result.eals_maxed
                    mv["maxed_enemy_health_level_skip"] = result.ehls_maxed
                    mv["maxed_enemy_attack_level_skip"] = result.eals_maxed
                    mv["ehls_completion_wave"] = result.ehls_wave
                    mv["eals_completion_wave"] = result.eals_wave
                    mv["eals_first_tap_wave"] = result.eals_first_tap_wave
                    mv["eals_first_tap_elapsed_s"] = result.eals_first_tap_elapsed_s
                    mv["level_skip_elapsed_s"] = result.elapsed_s
                    mv["level_skip_taps_sent"] = result.taps_sent
                    mv["level_skip_last_reason"] = result.reason
                log_mission(
                    f"[RUN_INIT] Fast level-skip result success={result.success} "
                    f"reason={result.reason} elapsed={result.elapsed_s:.2f}s "
                    f"waves=({result.ehls_wave},{result.eals_wave}) "
                    f"eals_first_tap=({result.eals_first_tap_wave},"
                    f"{result.eals_first_tap_elapsed_s}) taps={result.taps_sent}",
                    "INFO" if result.success else "WARN",
                )
            elif t == "damage_slider_configure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip damage_slider_configure while "
                        f"state={last_state}",
                        "DEBUG",
                    )
                    continue
                mode = str(act.get("mode") or "").strip().lower()
                result = configure_damage_slider(
                    act.get("value"),
                    mode=mode,
                )
                payload = result.as_dict()
                if mv is not None:
                    mv["damage_slider_observation"] = payload
                    if mode == "enforce":
                        mv["damage_slider_checked"] = result.success
                    elif mode == "observe":
                        mv["damage_slider_observed"] = True
                log_mission(
                    "[DAMAGE_SLIDER] "
                    f"mode={mode} expected={result.expected} "
                    f"initial={result.initial} final={result.final} "
                    f"steps={result.steps} success={result.success} "
                    f"reason={result.reason}",
                    "INFO" if result.success or mode == "observe" else "WARN",
                )
            elif t == "target_priority_ensure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(f"[EXEC] Skip target_priority_ensure while state={last_state}", "DEBUG")
                    continue
                expected_order = act.get("order")
                if expected_order is None:
                    ok = ensure_target_priority_order()
                else:
                    ok = ensure_target_priority_order(expected=expected_order)
                if mv is not None:
                    mv["target_priority_checked"] = ok
                log_mission(f"[EXEC] target_priority_ensure verified={ok}", "INFO" if ok else "WARN")
            elif t == "target_priority_observe":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip target_priority_observe while state={last_state}",
                        "DEBUG",
                    )
                    continue
                expected_order = act.get("order")
                if expected_order is None:
                    observation = observe_target_priority_order()
                else:
                    observation = observe_target_priority_order(
                        expected=expected_order
                    )
                if mv is not None:
                    mv["target_priority_observed"] = True
                    mv["target_priority_observation"] = observation.as_dict()
                log_mission(
                    "[EXEC] target_priority_observe "
                    f"observed={observation.observed} matches={observation.matches}",
                    "INFO" if observation.observed else "WARN",
                )
            elif t in {"gc_session_preflight", "session_preflight"}:
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip {t} while state={last_state}",
                        "DEBUG",
                    )
                    continue
                requirements = act.get("requirements")
                if not isinstance(requirements, dict):
                    log_mission(
                        "[SESSION_PREFLIGHT] Missing profile requirements",
                        "ERROR",
                    )
                    continue
                validator = str(act.get("validator") or "farm").strip().lower()
                if validator not in {"farm", "tournament"}:
                    log_mission(
                        f"[SESSION_PREFLIGHT] Unsupported validator {validator!r}",
                        "ERROR",
                    )
                    continue
                if mv is not None:
                    mv["gc_session_preflight_attempted"] = True
                    mv["gc_session_preflight_blocked"] = False
                if validator == "tournament":
                    result = run_read_only_gc_preflight(
                        requirements,
                        validate_fn=validate_tournament_session_preflight_screens,
                    )
                else:
                    result = run_read_only_gc_preflight(requirements)
                evidence_payload = (
                    result.evidence.as_dict()
                    if result.evidence is not None
                    else {}
                )
                if mv is not None:
                    mv["gc_session_preflight_last_status"] = result.status.value
                    mv["gc_session_preflight_last_reason"] = result.reason
                    mv["gc_session_preflight_evidence"] = evidence_payload
                if result.status is GcPreflightNavigationStatus.COMPLETE:
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = True
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                    log_mission(
                        "[SESSION_PREFLIGHT] Session validation completed: "
                        + json.dumps(evidence_payload, sort_keys=True),
                        "INFO",
                    )
                elif result.status is GcPreflightNavigationStatus.MISMATCH:
                    repairable = bool(
                        act.get("allow_repair", True)
                        and result.evidence is not None
                        and getattr(
                            result.evidence,
                            "requires_no_battle_repair",
                            False,
                        )
                    )
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = False
                        mv["gc_session_preflight_blocked"] = True
                        mv["gc_session_preflight_repair_required"] = repairable
                        mv["gc_session_preflight_repair_in_progress"] = False
                        if repairable:
                            mv["gc_no_battle_setup_completed"] = False
                    if repairable:
                        log_mission(
                            "[SESSION_PREFLIGHT] No-battle configuration mismatch; "
                            "guarded stop/repair/restart requested: "
                            + json.dumps(evidence_payload, sort_keys=True),
                            "WARN",
                        )
                    else:
                        log_mission(
                            "[SESSION_PREFLIGHT] Configuration mismatch is not "
                            "repairable at Home; automation remains blocked: "
                            + json.dumps(evidence_payload, sort_keys=True),
                            "WARN",
                        )
                else:
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = False
                        mv["gc_session_preflight_attempted"] = False
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                    interrupted_level = (
                        "INFO"
                        if result.status
                        is GcPreflightNavigationStatus.BATTLE_ENDED
                        else "WARN"
                    )
                    log_mission(
                        f"[SESSION_PREFLIGHT] Validation interrupted "
                        f"status={result.status.value} reason={result.reason}",
                        interrupted_level,
                    )
            elif t in {"ultimate_set_all_on", "ultimate_ensure_state"}:
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip {t} while state={last_state}",
                        "DEBUG",
                    )
                    continue
                now = time.time()
                targets = None
                if isinstance(act, dict):
                    targets = act.get("targets")
                if mv is not None:
                    next_ts = float(mv.get("ultimate_next_check_ts") or 0.0)
                    if now < next_ts:
                        mv["ultimate_checked"] = True
                        log_mission(
                            f"[ULTIMATE] Skip toggle sweep (next check in {int(next_ts - now)}s)",
                            "DEBUG",
                        )
                        continue
                    if targets is None:
                        prefs = mv.get("ultimate_targets")
                        if isinstance(prefs, list):
                            targets = prefs
                ensure_ultimate_state(target_prefs=targets)
                if mv is not None:
                    mv["ultimate_checked"] = True
                    mv["ultimate_next_check_ts"] = now + 300.0
                    mv["ultimate_last_checked_ts"] = now
                    if isinstance(targets, list):
                        mv["ultimate_targets"] = targets
            else:
                log(f"[EXEC] Unknown action: {act}", "WARN")
        except Exception as e:
            log(f"[EXEC] Exception during action {act}: {e}", "ERROR")


def _sleep_ms(milliseconds: int) -> None:
    if milliseconds <= 0:
        return
    from time import sleep

    sleep(milliseconds / 1000.0)


def _slugify_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug


_CARD_ENTRY_KEYS = {
    "navigation.Cards",
}

_CARD_INTERACTION_KEYS = {
    "buttons.Cards:GCFarmEarly",
    "buttons.Cards:GCFarmLate",
    "buttons.cards:locked:ok",
    "buttons.cards:locked:cancel",
}

_CARD_EXIT_KEYS = {
    "buttons.return_to_game",
}


def _maybe_suspend_blind_tapper_for_cards(key: str, mv: Optional[Dict[str, Any]]) -> None:
    if mv is None:
        return
    paused_flag = "blind_tapper_paused_for_cards"
    if key in _CARD_ENTRY_KEYS or key in _CARD_INTERACTION_KEYS:
        if mv.get(paused_flag):
            return
        if is_blind_gem_tapper_active():
            if stop_blind_gem_tapper():
                log("[EXEC] Paused blind gem tapper before card interaction", "INFO")
                mv[paused_flag] = True
        return

    if key in _CARD_EXIT_KEYS:
        if mv.pop(paused_flag, False):
            log("[EXEC] Resuming blind gem tapper after card interaction", "INFO")
            start_blind_gem_tapper(duration=10, interval=1, blocking=False)
