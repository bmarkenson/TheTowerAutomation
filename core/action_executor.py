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

import copy
import json
import re
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

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
from core.damage_adjuster import (
    configure_damage_slider,
    format_damage_percentage,
)
from core.orb_distance import configure_orb_distance
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    run_read_only_gc_preflight,
)
from core.gc_preflight import (
    summarize_gc_preflight_mismatch,
    summarize_gc_preflight_variations,
)
from core.gate_decisions import merge_profile_skip_waivers
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


def _repair_mismatch_attempt_limit(action: Mapping[str, Any]) -> int:
    """Return a safe, backwards-compatible retry threshold for one action."""

    try:
        attempts = int(action.get("repair_mismatch_attempts", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, attempts)


def _repair_mismatch_failure_key(
    failed_checks: Iterable[Any],
    *,
    reason: str,
) -> str:
    """Identify consecutive authoritative mismatches without volatile detail."""

    normalized = sorted(
        {
            str(check).strip()
            for check in failed_checks
            if str(check).strip()
        }
    )
    return json.dumps(normalized or [str(reason).strip()], sort_keys=True)


def _reset_repair_mismatch_attempts(mv: Dict[str, Any]) -> None:
    """Clear only the automatic retry evidence owned by session preflight."""

    mv["gc_session_preflight_repair_attempts"] = 0
    mv["gc_session_preflight_repair_failure_key"] = ""


def _bind_save_backed_home_evidence(
    setup_evidence: Mapping[str, Any],
    player_save_preflight: Any,
) -> dict[str, Any]:
    """Retain save-backed sections only through their exact bound carry."""

    payload = copy.deepcopy(dict(setup_evidence))
    configuration = payload.get("configuration")
    save_sections = (
        configuration.get("save_backed_sections")
        if isinstance(configuration, Mapping)
        else None
    )
    consume = getattr(player_save_preflight, "consume", None)
    if isinstance(save_sections, Mapping) and save_sections:
        section_checks = {
            "cards": "cards_deck",
            "workshop": "workshop_preset",
            "bots": "bots_preset",
            "guardians": "guardian_chips",
        }
        if not callable(consume) or any(
            consume(section_checks[section]) is None
            for section in save_sections
            if section in section_checks
        ):
            payload.pop("configuration", None)

    lock_evidence = payload.get("free_upgrade_locks")
    if (
        isinstance(lock_evidence, Mapping)
        and lock_evidence.get("source") == "player_save_preflight"
    ):
        carried_locks = (
            consume("free_upgrade_locks") if callable(consume) else None
        )
        expected_locks = lock_evidence.get("required")
        if (
            not isinstance(carried_locks, list)
            or not isinstance(expected_locks, list)
            or len(set(expected_locks)) != len(expected_locks)
            or not set(expected_locks).issubset(set(carried_locks))
        ):
            payload.pop("free_upgrade_locks", None)
            invalidate = getattr(player_save_preflight, "invalidate", None)
            if carried_locks is not None and callable(invalidate):
                invalidate("free_upgrade_lock_boundary_requirement_changed")
        else:
            bound_locks = dict(lock_evidence)
            bound_locks["source"] = "bound_player_save_preflight"
            bound_locks["observed"] = list(carried_locks)
            bound_locks["diagnostics"] = {
                **dict(bound_locks.get("diagnostics") or {}),
                "unmanaged_locks": sorted(
                    set(carried_locks) - set(expected_locks)
                ),
            }
            payload["free_upgrade_locks"] = bound_locks

    modules = payload.get("modules")
    if (
        isinstance(modules, Mapping)
        and modules.get("source") == "player_save_preflight"
    ):
        carried_modules = consume("modules") if callable(consume) else None
        expected_modules = {
            str(slot.get("slot_key") or ""): str(slot.get("expected") or "")
            for slot in modules.get("slots") or ()
            if isinstance(slot, Mapping)
        }
        observed_modules = {
            str(slot.get("slot_key") or ""): str(slot.get("actual") or "")
            for slot in modules.get("slots") or ()
            if isinstance(slot, Mapping)
            and isinstance(slot.get("actual"), str)
            and bool(str(slot.get("actual")).strip())
        }
        normalized_carried_modules = None
        if isinstance(carried_modules, Mapping) and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            and bool(value.strip())
            for key, value in carried_modules.items()
        ):
            normalized_carried_modules = {
                key.strip(): value.strip()
                for key, value in carried_modules.items()
            }
        module_mode = str(modules.get("mode") or "enforce")
        carried_modules_match = (
            normalized_carried_modules is not None
            and bool(expected_modules)
            and set(normalized_carried_modules) == set(expected_modules)
            and (
                (
                    module_mode == "observe"
                    and normalized_carried_modules == observed_modules
                )
                or (
                    module_mode != "observe"
                    and normalized_carried_modules == expected_modules
                )
            )
        )
        if not carried_modules_match:
            payload.pop("modules", None)
            invalidate = getattr(player_save_preflight, "invalidate", None)
            if carried_modules is not None and callable(invalidate):
                invalidate("module_boundary_requirement_changed")
        else:
            bound_modules = dict(modules)
            bound_modules["source"] = "bound_player_save_preflight"
            payload["modules"] = bound_modules

    ultimate = payload.get("ultimate_weapons")
    if (
        isinstance(ultimate, Mapping)
        and ultimate.get("source") == "player_save_preflight"
    ):
        # The in-battle route binds this component directly from the typed
        # carry.  Never let an unbound copy from Home setup bypass continuity.
        payload.pop("ultimate_weapons", None)
    return payload


def execute_actions(
    screen,
    actions: Iterable[Action],
    ctx: Optional[MissionContext] = None,
    *,
    action_guard_fn: Optional[Callable[[], bool]] = None,
) -> None:
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

            # A main-loop decision is not reusable input authority. Refresh
            # the central guard immediately before materializing each action;
            # bounded multi-input routes continue to receive the same guard
            # for their own per-input checks.
            if (
                t != "sleep"
                and action_guard_fn is not None
                and not action_guard_fn()
            ):
                log_mission(
                    f"[EXEC] Skip {t or 'unknown'} because action authority was lost",
                    "DEBUG",
                )
                continue

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
                    f"mode={mode} "
                    f"expected={format_damage_percentage(result.expected)} "
                    f"initial={format_damage_percentage(result.initial)} "
                    f"final={format_damage_percentage(result.final)} "
                    f"steps={result.steps} success={result.success} "
                    f"reason={result.reason}",
                    "INFO" if result.success or mode == "observe" else "WARN",
                )
            elif t == "orb_distance_configure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip orb_distance_configure while "
                        f"state={last_state}",
                        "DEBUG",
                    )
                    continue
                mode = str(act.get("mode") or "").strip().lower()
                orb_distance_kwargs = {
                    "range_basis": act.get("range_basis"),
                    "extra": act.get("extra"),
                    "workshop": act.get("workshop"),
                    "mode": mode,
                }
                if "range_presets" in act:
                    orb_distance_kwargs["range_presets"] = act.get(
                        "range_presets"
                    )
                if action_guard_fn is not None:
                    orb_distance_kwargs["action_guard_fn"] = action_guard_fn
                result = configure_orb_distance(
                    **orb_distance_kwargs,
                )
                payload = result.as_dict()
                if mv is not None:
                    mv["orb_distance_observation"] = payload
                    if mode == "enforce":
                        mv["orb_distance_checked"] = result.success
                    elif mode == "observe":
                        mv["orb_distance_observed"] = True
                log_mission(
                    "[ORB_DISTANCE] "
                    f"mode={mode} range={result.range_observed}/"
                    f"{result.range_basis} "
                    f"expected=({result.expected_extra},"
                    f"{result.expected_workshop}) "
                    f"initial=({result.initial_extra},"
                    f"{result.initial_workshop}) "
                    f"final=({result.final_extra},{result.final_workshop}) "
                    f"steps=({result.extra_steps},{result.workshop_steps}) "
                    f"success={result.success} reason={result.reason}",
                    "INFO" if result.success or mode == "observe" else "WARN",
                )
            elif t == "target_priority_ensure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(f"[EXEC] Skip target_priority_ensure while state={last_state}", "DEBUG")
                    continue
                expected_order = act.get("order")
                save_coordinator = (
                    ctx.data.get("player_save_preflight_coordinator")
                    if ctx is not None
                    else None
                )
                carried_order = (
                    save_coordinator.consume("target_priority")
                    if callable(getattr(save_coordinator, "consume", None))
                    else None
                )
                target_kwargs: Dict[str, Any] = {}
                target_repaired = False

                def observe_target_repair() -> None:
                    nonlocal target_repaired
                    target_repaired = True

                record_ui_verification = getattr(
                    save_coordinator,
                    "record_ui_verification",
                    None,
                )
                if callable(record_ui_verification) or callable(
                    getattr(save_coordinator, "invalidate", None)
                ):
                    target_kwargs["repair_observer_fn"] = observe_target_repair
                used_ui = False
                ui_contradiction = False
                if (
                    isinstance(carried_order, list)
                    and isinstance(expected_order, list)
                    and carried_order == expected_order
                ):
                    ok = True
                    if mv is not None:
                        mv["target_priority_evidence"] = {
                            "source": "bound_player_save_preflight",
                            "checked": False,
                            "valid": True,
                            "order": list(carried_order),
                        }
                elif carried_order is not None:
                    if callable(getattr(save_coordinator, "invalidate", None)):
                        save_coordinator.invalidate(
                            "target_priority_action_requirement_changed"
                        )
                    used_ui = True
                    ok = ensure_target_priority_order(
                        expected=expected_order,
                        **target_kwargs,
                    )
                elif expected_order is None:
                    used_ui = True
                    ok = ensure_target_priority_order(**target_kwargs)
                else:
                    used_ui = True
                    ok = ensure_target_priority_order(
                        expected=expected_order,
                        **target_kwargs,
                    )
                if used_ui and ok and callable(record_ui_verification):
                    ui_verified = record_ui_verification(
                        "target_priority",
                        changed=target_repaired,
                    ) is not False
                    ui_contradiction = not ui_verified
                    ok = ui_verified
                if used_ui and mv is not None:
                    decision_fn = getattr(save_coordinator, "decision", None)
                    save_decision = (
                        decision_fn("target_priority")
                        if callable(decision_fn)
                        else {}
                    )
                    mv["target_priority_evidence"] = {
                        "source": "ui",
                        "checked": True,
                        "valid": bool(ok),
                        "status": (
                            "contradiction"
                            if ui_contradiction
                            else "ui_verified_repair"
                            if ok and target_repaired
                            else "ui_verified"
                            if ok
                            else "ui_verification_failed"
                        ),
                        "changed": target_repaired,
                        "save_disposition": save_decision.get("disposition"),
                    }
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
                    mv["gc_session_preflight_restart_available"] = False
                effective_requirements = dict(requirements)
                runtime_waivers = (
                    mv.get("gc_session_preflight_waivers")
                    if mv is not None
                    else None
                )
                waivers = merge_profile_skip_waivers(
                    requirements,
                    runtime_waivers
                    if isinstance(runtime_waivers, Mapping)
                    else None,
                )
                if waivers:
                    effective_requirements["_gate_waivers"] = waivers
                preflight_kwargs: Dict[str, Any] = {}
                if mv is not None:
                    save_coordinator = ctx.data.get(
                        "player_save_preflight_coordinator"
                    )
                    setup_evidence = mv.get("gc_no_battle_setup_evidence")
                    if (
                        mv.get("gc_no_battle_setup_completed")
                        and isinstance(setup_evidence, Mapping)
                    ):
                        bound_setup_evidence = _bind_save_backed_home_evidence(
                            setup_evidence,
                            save_coordinator,
                        )
                        preflight_kwargs["no_battle_setup_evidence"] = (
                            bound_setup_evidence
                        )
                    else:
                        bound_setup_evidence = setup_evidence
                    lock_evidence = (
                        bound_setup_evidence.get("free_upgrade_locks")
                        if isinstance(bound_setup_evidence, Mapping)
                        else None
                    )
                    if isinstance(lock_evidence, Mapping):
                        preflight_kwargs["free_upgrade_lock_boundary_evidence"] = (
                            dict(lock_evidence)
                        )
                    if callable(getattr(save_coordinator, "consume", None)):
                        preflight_kwargs["player_save_preflight"] = (
                            save_coordinator
                        )
                if (
                    act.get("stay_in_battle_when_attached") is True
                    and ctx.data.get("startup_gates_deferred") is True
                ):
                    preflight_kwargs["stay_in_battle"] = True
                if validator == "tournament":
                    result = run_read_only_gc_preflight(
                        effective_requirements,
                        validate_fn=validate_tournament_session_preflight_screens,
                        **preflight_kwargs,
                    )
                else:
                    result = run_read_only_gc_preflight(
                        effective_requirements,
                        **preflight_kwargs,
                    )
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
                        mv["gc_session_preflight_failed_checks"] = []
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = False
                        _reset_repair_mismatch_attempts(mv)
                    variation_summary = summarize_gc_preflight_variations(
                        evidence_payload
                    )
                    completion = "[SESSION_PREFLIGHT] Session validation completed"
                    if variation_summary:
                        completion += (
                            "; module variation observed — " + variation_summary
                        )
                    log_mission(completion, "INFO")
                    log_mission(
                        "[SESSION_PREFLIGHT] completed_evidence="
                        + json.dumps(evidence_payload, sort_keys=True),
                        "DEBUG",
                    )
                elif result.status is GcPreflightNavigationStatus.MISMATCH:
                    mismatch_policy = str(
                        act.get("mismatch_policy") or "block"
                    ).strip().lower()
                    observation_only = mismatch_policy == "notify"
                    home_repair_available = bool(
                        result.evidence is not None
                        and getattr(
                            result.evidence,
                            "requires_no_battle_repair",
                            False,
                        )
                    )
                    repairable = bool(
                        not observation_only
                        and act.get("allow_repair", True)
                        and home_repair_available
                    )
                    failed_checks = list(
                        getattr(result.evidence, "failed_checks", ())
                    )
                    mismatch_summary = summarize_gc_preflight_mismatch(
                        evidence_payload
                    )
                    repair_requested = repairable
                    retry_attempt = 0
                    retry_limit = _repair_mismatch_attempt_limit(act)
                    if repairable and mv is not None:
                        failure_key = _repair_mismatch_failure_key(
                            failed_checks,
                            reason=result.reason,
                        )
                        prior_key = str(
                            mv.get("gc_session_preflight_repair_failure_key")
                            or ""
                        )
                        try:
                            prior_attempts = (
                                int(
                                    mv.get(
                                        "gc_session_preflight_repair_attempts"
                                    )
                                    or 0
                                )
                                if prior_key == failure_key
                                else 0
                            )
                        except (TypeError, ValueError):
                            prior_attempts = 0
                        retry_attempt = prior_attempts + 1
                        repair_requested = retry_attempt >= retry_limit
                        mv["gc_session_preflight_repair_attempts"] = (
                            retry_attempt
                        )
                        mv["gc_session_preflight_repair_failure_key"] = (
                            failure_key
                        )
                    elif mv is not None:
                        _reset_repair_mismatch_attempts(mv)
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = False
                        mv["gc_session_preflight_blocked"] = bool(
                            not observation_only
                            and (not repairable or repair_requested)
                        )
                        mv["gc_session_preflight_failed_checks"] = failed_checks
                        mv["gc_session_preflight_repair_required"] = (
                            repair_requested
                        )
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = (
                            home_repair_available
                        )
                        if repairable and not repair_requested:
                            mv["gc_session_preflight_attempted"] = False
                        if repair_requested:
                            mv["gc_no_battle_setup_completed"] = False
                    if repairable and not repair_requested:
                        log_mission(
                            "[SESSION_PREFLIGHT] Transient no-battle "
                            "configuration mismatch "
                            f"(attempt {retry_attempt} of {retry_limit}) — "
                            f"{mismatch_summary}. Read-only validation will "
                            "retry after cooldown.",
                            "INFO",
                        )
                    elif repair_requested:
                        log_mission(
                            "[SESSION_PREFLIGHT] No-battle configuration mismatch; "
                            f"{retry_attempt} matching attempts exhausted — "
                            f"{mismatch_summary}. Guarded stop/repair/restart "
                            "requested.",
                            "WARN",
                        )
                    elif observation_only:
                        log_mission(
                            "[SESSION_PREFLIGHT] Read-only observer mismatch "
                            f"recorded — {mismatch_summary}. Observation and "
                            "terminal capture continue without operator action.",
                            "WARN",
                        )
                    else:
                        log_mission(
                            "[SESSION_PREFLIGHT] Configuration mismatch is not "
                            f"repairable at Home — {mismatch_summary}. Automation "
                            "remains blocked.",
                            "WARN",
                        )
                    log_mission(
                        "[SESSION_PREFLIGHT] mismatch_evidence="
                        + json.dumps(evidence_payload, sort_keys=True),
                        "DEBUG",
                    )
                else:
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = False
                        mv["gc_session_preflight_attempted"] = False
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = False
                        mv["gc_session_preflight_failed_checks"] = []
                        _reset_repair_mismatch_attempts(mv)
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
