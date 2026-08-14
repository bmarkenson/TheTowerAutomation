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
from core.player_save_mapping_candidates import (
    build_mapping_candidate_ui_evidence,
)
from core.level_skip_initializer import initialize_level_skips
from core.damage_adjuster import (
    configure_damage_slider,
    format_damage_percentage,
    normalize_damage_percentage,
)
from core.orb_distance import (
    configure_orb_distance,
    normalize_orb_distance_preset,
    normalize_orb_distance_presets,
)
from core.gc_preflight_navigation import (
    GcPreflightNavigationStatus,
    run_read_only_gc_preflight,
)
from core.gc_preflight import (
    configuration_ui_boundary_sections,
    summarize_gc_preflight_mismatch,
    summarize_gc_preflight_variations,
)
from core.gate_decisions import merge_profile_skip_waivers
from core.runtime_failure_policy import (
    RuntimeFailureKind,
    decide_runtime_failure,
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


def _reset_repair_mismatch_attempts(mv: Dict[str, Any]) -> None:
    """Clear legacy automatic-repair counters after a completed check."""

    mv["gc_session_preflight_repair_attempts"] = 0
    mv["gc_session_preflight_repair_failure_key"] = ""


def _record_attached_rule_disposition(
    mv: Dict[str, Any],
    *,
    rule_id: str,
    disposition: str,
    action: str,
) -> None:
    """Make one attached-validation rule terminal for this attachment."""

    if not rule_id:
        return
    dispositions = mv.setdefault("attached_validation_rule_dispositions", {})
    if isinstance(dispositions, dict):
        dispositions[rule_id] = {
            "disposition": disposition,
            "action": action,
        }


def _record_attached_observation_result(
    mv: Dict[str, Any],
    *,
    check_id: str,
    matched: bool,
    reason: str,
) -> None:
    """Complete one attached read-only check and retain any degradation."""

    mv[f"{check_id}_checked"] = True
    if matched:
        return

    raw_failed_checks = mv.get("gc_session_preflight_failed_checks", ())
    if not isinstance(raw_failed_checks, (list, tuple, set, frozenset)):
        raw_failed_checks = ()
    failed_checks = {
        str(value).strip()
        for value in raw_failed_checks
        if str(value).strip()
    }
    failed_checks.add(check_id)
    detail = str(reason or "attached observation unavailable").strip()
    message = f"{check_id} attached observation: {detail}"
    prior_reason = str(mv.get("gc_session_preflight_last_reason") or "").strip()
    mv["gc_session_preflight_degraded"] = True
    mv["gc_session_preflight_disposition"] = "continue_degraded"
    mv["gc_session_preflight_failed_checks"] = sorted(failed_checks)
    mv["gc_session_preflight_last_reason"] = (
        "; ".join(dict.fromkeys((prior_reason, message)))
        if prior_reason
        else message
    )
    report = mv.get("gc_session_preflight_evidence")
    report_payload = dict(report) if isinstance(report, Mapping) else {}
    raw_report_checks = report_payload.get("failed_checks", ())
    if not isinstance(raw_report_checks, (list, tuple, set, frozenset)):
        raw_report_checks = ()
    report_checks = {
        str(value).strip()
        for value in raw_report_checks
        if str(value).strip()
    }
    report_checks.add(check_id)
    attached_controls = report_payload.get("attached_control_checks")
    attached_control_payload = (
        dict(attached_controls)
        if isinstance(attached_controls, Mapping)
        else {}
    )
    attached_control_payload[check_id] = {
        "valid": False,
        "disposition": "continue_degraded",
        "reason": detail,
    }
    report_payload.update(
        {
            "valid": False,
            "degraded": True,
            "disposition": "continue_degraded",
            "failed_checks": sorted(report_checks),
            "attached_control_checks": attached_control_payload,
        }
    )
    mv["gc_session_preflight_evidence"] = report_payload

    existing = mv.get("gc_running_configuration_degradation")
    existing_payload = dict(existing) if isinstance(existing, Mapping) else {}
    raw_explicit_checks = existing_payload.get("failed_checks", ())
    if not isinstance(raw_explicit_checks, (list, tuple, set, frozenset)):
        raw_explicit_checks = ()
    explicit_checks = {
        str(value).strip()
        for value in raw_explicit_checks
        if str(value).strip()
    }
    explicit_checks.add(check_id)
    explicit_reason = str(existing_payload.get("reason") or "").strip()
    raw_sources = existing_payload.get("sources", ())
    if not isinstance(raw_sources, (list, tuple, set, frozenset)):
        raw_sources = ()
    explicit_sources = {
        str(value).strip()
        for value in raw_sources
        if str(value).strip()
    }
    existing_source = str(existing_payload.get("source") or "").strip()
    if existing_source:
        explicit_sources.add(existing_source)
    explicit_sources.add("attachment_observation")
    raw_reasons_by_source = existing_payload.get("reasons_by_source")
    reasons_by_source = (
        {
            str(key).strip(): str(value).strip()
            for key, value in raw_reasons_by_source.items()
            if str(key).strip() and str(value).strip()
        }
        if isinstance(raw_reasons_by_source, Mapping)
        else {}
    )
    if (
        existing_source
        and explicit_reason
        and existing_source not in reasons_by_source
    ):
        reasons_by_source[existing_source] = explicit_reason
    prior_observation_reason = reasons_by_source.get(
        "attachment_observation",
        "",
    )
    reasons_by_source["attachment_observation"] = (
        "; ".join(
            dict.fromkeys((prior_observation_reason, message))
        )
        if prior_observation_reason
        else message
    )
    existing_payload.update(
        {
            "schema_version": 1,
            "source": "attachment_observation",
            "sources": sorted(explicit_sources),
            "reason": "; ".join(
                dict.fromkeys(reasons_by_source.values())
            ),
            "reasons_by_source": reasons_by_source,
            "failed_checks": sorted(explicit_checks),
        }
    )
    mv["gc_running_configuration_degradation"] = existing_payload


def _complete_session_preflight_degraded(
    mv: Optional[Dict[str, Any]],
    *,
    reason: str,
    failed_checks: Iterable[str] = (),
) -> None:
    """Release a recoverable validation failure under the global policy."""

    if mv is None:
        return
    normalized_reason = str(reason or "validation unavailable").strip()
    checks = sorted(
        {
            str(check_id).strip()
            for check_id in failed_checks
            if str(check_id).strip()
        }
    )
    decision = decide_runtime_failure(RuntimeFailureKind.VALIDATION_UNAVAILABLE)
    mv["gc_session_preflight_attempted"] = True
    mv["gc_session_preflight_completed"] = True
    mv["gc_session_preflight_blocked"] = False
    mv["gc_session_preflight_degraded"] = True
    mv["gc_session_preflight_disposition"] = decision.disposition.value
    mv["gc_session_preflight_last_status"] = "unavailable"
    mv["gc_session_preflight_last_reason"] = normalized_reason
    mv["gc_session_preflight_failed_checks"] = checks
    mv["gc_session_preflight_repair_required"] = False
    mv["gc_session_preflight_repair_in_progress"] = False
    mv["gc_session_preflight_restart_available"] = False
    mv["gc_session_preflight_evidence"] = {
        "valid": False,
        "degraded": True,
        "disposition": decision.disposition.value,
        "failed_checks": checks,
        "reason": normalized_reason,
    }
    _reset_repair_mismatch_attempts(mv)


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
    retained_ui_sections = configuration_ui_boundary_sections(configuration)
    if isinstance(save_sections, Mapping) and save_sections:
        section_checks = {
            "cards": "cards_deck",
            "workshop": "workshop_preset",
            "bots": "bots_preset",
            "guardians": "guardian_chips",
        }
        bound_save_sections: dict[str, dict[str, Any]] = {}
        binding_complete = callable(consume)
        for raw_section, provenance in save_sections.items():
            section = str(raw_section or "").strip()
            check_id = section_checks.get(section)
            if check_id is None or not callable(consume):
                binding_complete = False
                continue
            carried = consume(check_id)
            if carried is None:
                binding_complete = False
                continue
            normalized_provenance = (
                dict(provenance) if isinstance(provenance, Mapping) else {}
            )
            normalized_provenance.update(
                disposition="save_match",
                source="bound_player_save_preflight",
            )
            bound_save_sections[section] = normalized_provenance
        if not binding_complete or len(bound_save_sections) != len(save_sections):
            payload.pop("configuration", None)
            if retained_ui_sections:
                payload["configuration_ui_boundary_sections"] = (
                    retained_ui_sections
                )
        else:
            bound_configuration = dict(configuration)
            bound_configuration["save_backed_sections"] = bound_save_sections
            payload["configuration"] = bound_configuration

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
            fallback = getattr(player_save_preflight, "fallback_checks", None)
            if carried_locks is not None and callable(fallback):
                fallback(
                    "free_upgrade_lock_boundary_requirement_changed",
                    check_ids=("free_upgrade_locks",),
                )
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
            fallback = getattr(player_save_preflight, "fallback_checks", None)
            if carried_modules is not None and callable(fallback):
                fallback(
                    "module_boundary_requirement_changed",
                    check_ids=("modules",),
                )
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
    attachment_context = bool(
        ctx is not None
        and (
            ctx.data.get("startup_gates_deferred") is True
            or ctx.data.get("manual_return_reconciliation_active") is True
        )
    )
    for act in actions or []:
        t = None
        attachment_validation = False
        attachment_rule_id = ""
        try:
            t = (act or {}).get("type")
            is_strategy_action = bool((act or {}).get("_strategy"))
            if is_strategy_action and isinstance(act, dict):
                act.pop("_strategy", None)
            if isinstance(act, dict):
                attachment_validation = bool(
                    act.pop("_attachment_validation", False)
                )
                attachment_rule_id = str(
                    act.pop("_attachment_rule_id", "") or ""
                ).strip()
            if attachment_validation and t == "target_priority_ensure":
                # Attach may inspect this normally repairable setting, but the
                # accepted battle must remain untouched.
                t = "target_priority_observe"

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

            attachment_safe_actions = {
                "damage_slider_configure",
                "gc_session_preflight",
                "orb_distance_configure",
                "session_preflight",
                "sleep",
                "target_priority_observe",
            }
            if attachment_validation and t not in attachment_safe_actions:
                action_name = str(t or "unknown").strip().lower() or "unknown"
                check_id = f"attached_action_{action_name}"
                if mv is not None:
                    _record_attached_observation_result(
                        mv,
                        check_id=check_id,
                        matched=False,
                        reason=(
                            f"action {action_name!r} has no read-only Attach "
                            "disposition and was suppressed"
                        ),
                    )
                    _record_attached_rule_disposition(
                        mv,
                        rule_id=attachment_rule_id,
                        disposition="suppressed_degraded",
                        action=action_name,
                    )
                log_mission(
                    "[ATTACH] Suppressed non-read-only attached-validation "
                    f"action {action_name!r}; Automation continues degraded",
                    "WARN",
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
                save_coordinator = (
                    ctx.data.get(
                        "player_save_attachment_evidence"
                        if attachment_context
                        else "player_save_preflight_coordinator"
                    )
                    if ctx is not None
                    else None
                )
                invalidate_snapshot = getattr(
                    save_coordinator,
                    "invalidate",
                    None,
                )
                close_mapping_window = getattr(
                    save_coordinator,
                    "close_mapping_candidate_window",
                    None,
                )

                def observe_level_skip_mutation() -> None:
                    if callable(invalidate_snapshot):
                        invalidate_snapshot(
                            "level_skip_mutation_started",
                            check_ids=(),
                        )
                    elif callable(close_mapping_window):
                        close_mapping_window("level_skip_mutation_started")

                level_skip_kwargs: Dict[str, Any] = {"screenshot": screen}
                if callable(invalidate_snapshot) or callable(
                    close_mapping_window
                ):
                    level_skip_kwargs["mutation_observer_fn"] = (
                        observe_level_skip_mutation
                    )
                result = initialize_level_skips(**level_skip_kwargs)
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
                requested_mode = str(act.get("mode") or "").strip().lower()
                mode = (
                    "observe"
                    if attachment_context and requested_mode == "enforce"
                    else requested_mode
                )
                save_coordinator = (
                    ctx.data.get(
                        "player_save_attachment_evidence"
                        if attachment_context
                        else "player_save_preflight_coordinator"
                    )
                    if ctx is not None
                    else None
                )
                consume_save = getattr(save_coordinator, "consume", None)
                carried_damage = (
                    consume_save("damage_slider")
                    if callable(consume_save)
                    else None
                )
                try:
                    expected_damage = normalize_damage_percentage(
                        act.get("value")
                    )
                except (TypeError, ValueError):
                    expected_damage = None
                try:
                    observed_damage = normalize_damage_percentage(
                        carried_damage
                    )
                except (TypeError, ValueError):
                    observed_damage = None
                damage_matches = bool(
                    observed_damage is not None
                    and expected_damage is not None
                    and observed_damage == expected_damage
                )
                if (
                    mode in {"observe", "enforce"}
                    and
                    observed_damage is not None
                    and expected_damage is not None
                    and (damage_matches or mode == "observe")
                ):
                    save_reason = (
                        "bound_player_save_preflight"
                        if damage_matches
                        else "bound_player_save_observation"
                    )
                    payload = {
                        "mode": mode,
                        "expected": expected_damage,
                        "initial": observed_damage,
                        "final": observed_damage,
                        "observed": True,
                        "matches": damage_matches,
                        "changed": False,
                        "steps": 0,
                        "dismissed": True,
                        "reason": save_reason,
                        "success": damage_matches,
                        "source": "bound_player_save_preflight",
                    }
                    if mv is not None:
                        mv["damage_slider_observation"] = payload
                        if mode == "enforce":
                            mv["damage_slider_checked"] = True
                        elif mode == "observe":
                            mv["damage_slider_observed"] = True
                            if attachment_context:
                                _record_attached_observation_result(
                                    mv,
                                    check_id="damage_slider",
                                    matched=damage_matches,
                                    reason=save_reason,
                                )
                    log_mission(
                        "[DAMAGE_SLIDER] source=bound_player_save_preflight "
                        f"mode={mode} expected="
                        f"{format_damage_percentage(expected_damage)} "
                        f"observed={format_damage_percentage(observed_damage)} "
                        f"steps=0 success={damage_matches} "
                        f"reason={'save_match' if damage_matches else 'save_observation'}",
                        "INFO" if damage_matches or mode == "observe" else "WARN",
                    )
                    continue
                if carried_damage is not None:
                    fallback = getattr(save_coordinator, "fallback_checks", None)
                    if callable(fallback):
                        fallback(
                            "damage_slider_action_requirement_changed",
                            check_ids=("damage_slider",),
                        )
                record_mapping_observation = getattr(
                    save_coordinator,
                    "record_mapping_observation",
                    None,
                )
                close_mapping_window = getattr(
                    save_coordinator,
                    "close_mapping_candidate_window",
                    None,
                )
                invalidate_snapshot = getattr(
                    save_coordinator,
                    "invalidate",
                    None,
                )
                record_ui_verification = getattr(
                    save_coordinator,
                    "record_ui_verification",
                    None,
                )

                def observe_initial_damage_slider(reading: Any) -> None:
                    percentage = getattr(reading, "percentage", None)
                    if not callable(record_mapping_observation) or not percentage:
                        return
                    try:
                        record_mapping_observation(
                            "damage_slider",
                            build_mapping_candidate_ui_evidence(
                                "damage_slider",
                                canonical_values=[percentage],
                                locator_values={
                                    "damageAdjustmentLog": percentage,
                                },
                                locator_scopes={
                                    "damageAdjustmentLog": {
                                        "field": "damageAdjustmentLog",
                                    }
                                },
                            ),
                        )
                    except Exception as exc:
                        log(
                            "[PLAYER_SAVE_MAPPING] Initial Damage Slider "
                            f"observation failed: {exc}",
                            "DEBUG",
                        )

                def observe_damage_slider_repair() -> None:
                    if callable(invalidate_snapshot):
                        invalidate_snapshot(
                            "damage_slider_repair_started",
                            check_ids=("damage_slider",),
                        )
                    elif callable(close_mapping_window):
                        close_mapping_window("damage_slider_repair_started")

                damage_slider_kwargs: Dict[str, Any] = {"mode": mode}
                if callable(record_mapping_observation):
                    damage_slider_kwargs["initial_evidence_observer_fn"] = (
                        observe_initial_damage_slider
                    )
                if callable(invalidate_snapshot) or callable(
                    close_mapping_window
                ):
                    damage_slider_kwargs["repair_observer_fn"] = (
                        observe_damage_slider_repair
                    )
                result = configure_damage_slider(
                    act.get("value"),
                    **damage_slider_kwargs,
                )
                payload = result.as_dict()
                ui_verified = True
                if result.success and callable(record_ui_verification):
                    ui_verified = (
                        record_ui_verification(
                            "damage_slider",
                            changed=bool(result.changed),
                        )
                        is not False
                    )
                    if not ui_verified:
                        payload["save_ui_verification"] = "contradiction"
                workflow_success = bool(result.success and ui_verified)
                if mv is not None:
                    mv["damage_slider_observation"] = payload
                    if mode == "enforce":
                        mv["damage_slider_checked"] = workflow_success
                    elif mode == "observe":
                        mv["damage_slider_observed"] = True
                        if attachment_context:
                            _record_attached_observation_result(
                                mv,
                                check_id="damage_slider",
                                matched=workflow_success,
                                reason=str(result.reason),
                            )
                log_mission(
                    "[DAMAGE_SLIDER] "
                    f"mode={mode} "
                    f"expected={format_damage_percentage(result.expected)} "
                    f"initial={format_damage_percentage(result.initial)} "
                    f"final={format_damage_percentage(result.final)} "
                    f"steps={result.steps} success={workflow_success} "
                    f"reason={result.reason}",
                    "INFO" if workflow_success else "WARN",
                )
            elif t == "orb_distance_configure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(
                        f"[EXEC] Skip orb_distance_configure while "
                        f"state={last_state}",
                        "DEBUG",
                    )
                    continue
                requested_mode = str(act.get("mode") or "").strip().lower()
                mode = (
                    "observe"
                    if attachment_context and requested_mode == "enforce"
                    else requested_mode
                )
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
                save_coordinator = (
                    ctx.data.get(
                        "player_save_attachment_evidence"
                        if attachment_context
                        else "player_save_preflight_coordinator"
                    )
                    if ctx is not None
                    else None
                )
                consume_save = getattr(save_coordinator, "consume", None)
                carried_orb = (
                    consume_save("orb_distance")
                    if callable(consume_save)
                    else None
                )
                orb_action_valid = mode in {"observe", "enforce"}
                configured_orb_presets = None
                try:
                    requested_orb = normalize_orb_distance_preset(
                        {
                            "range_basis": act.get("range_basis"),
                            "extra": act.get("extra"),
                            "workshop": act.get("workshop"),
                        }
                    )
                    if "range_presets" in act:
                        configured_orb_presets = normalize_orb_distance_presets(
                            act.get("range_presets")
                        )
                except (TypeError, ValueError):
                    requested_orb = None
                    orb_action_valid = False
                try:
                    observed_orb = normalize_orb_distance_preset(carried_orb)
                except (TypeError, ValueError):
                    observed_orb = None
                expected_orb = requested_orb
                if observed_orb is not None and configured_orb_presets is not None:
                    expected_orb = next(
                        (
                            preset
                            for preset in configured_orb_presets
                            if preset["range_basis"]
                            == observed_orb["range_basis"]
                        ),
                        None,
                    )
                orb_matches = bool(
                    observed_orb is not None
                    and expected_orb is not None
                    and observed_orb == expected_orb
                )
                if (
                    orb_action_valid
                    and
                    observed_orb is not None
                    and expected_orb is not None
                    and (orb_matches or mode == "observe")
                ):
                    save_reason = (
                        "bound_player_save_preflight"
                        if orb_matches
                        else "bound_player_save_observation"
                    )
                    payload = {
                        "mode": mode,
                        "range_basis": expected_orb["range_basis"],
                        "range_observed": observed_orb["range_basis"],
                        "expected_extra": expected_orb["extra"],
                        "expected_workshop": expected_orb["workshop"],
                        "initial_extra": observed_orb["extra"],
                        "initial_workshop": observed_orb["workshop"],
                        "final_extra": observed_orb["extra"],
                        "final_workshop": observed_orb["workshop"],
                        "observed": True,
                        "matches": orb_matches,
                        "changed": False,
                        "extra_steps": 0,
                        "workshop_steps": 0,
                        "dismissed": True,
                        "reason": save_reason,
                        "preserved": False,
                        "success": orb_matches,
                        "source": "bound_player_save_preflight",
                    }
                    if mv is not None:
                        mv["orb_distance_observation"] = payload
                        if mode == "enforce":
                            mv["orb_distance_checked"] = True
                        elif mode == "observe":
                            mv["orb_distance_observed"] = True
                            if attachment_context:
                                _record_attached_observation_result(
                                    mv,
                                    check_id="orb_distance",
                                    matched=orb_matches,
                                    reason=save_reason,
                                )
                    log_mission(
                        "[ORB_DISTANCE] source=bound_player_save_preflight "
                        f"mode={mode} range={observed_orb['range_basis']} "
                        f"expected=({expected_orb['extra']},"
                        f"{expected_orb['workshop']}) observed=("
                        f"{observed_orb['extra']},{observed_orb['workshop']}) "
                        f"steps=(0,0) success={orb_matches} "
                        f"reason={'save_match' if orb_matches else 'save_observation'}",
                        "INFO" if orb_matches or mode == "observe" else "WARN",
                    )
                    continue
                if carried_orb is not None:
                    fallback = getattr(save_coordinator, "fallback_checks", None)
                    if callable(fallback):
                        fallback(
                            "orb_distance_action_requirement_changed",
                            check_ids=("orb_distance",),
                        )
                record_mapping_observation = getattr(
                    save_coordinator,
                    "record_mapping_observation",
                    None,
                )
                close_mapping_window = getattr(
                    save_coordinator,
                    "close_mapping_candidate_window",
                    None,
                )
                invalidate_snapshot = getattr(
                    save_coordinator,
                    "invalidate",
                    None,
                )
                record_ui_verification = getattr(
                    save_coordinator,
                    "record_ui_verification",
                    None,
                )

                def observe_initial_orb_distance(
                    range_basis: str,
                    reading: Any,
                ) -> None:
                    if not callable(record_mapping_observation):
                        return
                    extra = getattr(reading, "extra", None)
                    workshop = getattr(reading, "workshop", None)
                    if not range_basis or extra is None or workshop is None:
                        return
                    locator_values = {
                        "rangeLevelSelected": range_basis,
                        "innerOrbDistance": extra,
                        "workshopOrbDistance": workshop,
                    }
                    try:
                        record_mapping_observation(
                            "orb_distance",
                            build_mapping_candidate_ui_evidence(
                                "orb_distance",
                                canonical_values=list(locator_values.values()),
                                locator_values=locator_values,
                                locator_scopes={
                                    field: {"field": field}
                                    for field in locator_values
                                },
                            ),
                        )
                    except Exception as exc:
                        log(
                            "[PLAYER_SAVE_MAPPING] Initial Orb Distance "
                            f"observation failed: {exc}",
                            "DEBUG",
                        )

                def observe_orb_distance_repair() -> None:
                    if callable(invalidate_snapshot):
                        invalidate_snapshot(
                            "orb_distance_repair_started",
                            check_ids=("orb_distance",),
                        )
                    elif callable(close_mapping_window):
                        close_mapping_window("orb_distance_repair_started")

                if callable(record_mapping_observation):
                    orb_distance_kwargs["initial_evidence_observer_fn"] = (
                        observe_initial_orb_distance
                    )
                if callable(invalidate_snapshot) or callable(
                    close_mapping_window
                ):
                    orb_distance_kwargs["repair_observer_fn"] = (
                        observe_orb_distance_repair
                    )
                result = configure_orb_distance(
                    **orb_distance_kwargs,
                )
                payload = result.as_dict()
                ui_verified = True
                if result.success and callable(record_ui_verification):
                    ui_verified = (
                        record_ui_verification(
                            "orb_distance",
                            changed=bool(result.changed),
                        )
                        is not False
                    )
                    if not ui_verified:
                        payload["save_ui_verification"] = "contradiction"
                workflow_success = bool(result.success and ui_verified)
                if mv is not None:
                    mv["orb_distance_observation"] = payload
                    if mode == "enforce":
                        mv["orb_distance_checked"] = workflow_success
                    elif mode == "observe":
                        mv["orb_distance_observed"] = True
                        if attachment_context:
                            _record_attached_observation_result(
                                mv,
                                check_id="orb_distance",
                                matched=workflow_success,
                                reason=str(result.reason),
                            )
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
                    f"success={workflow_success} reason={result.reason}",
                    "INFO" if workflow_success else "WARN",
                )
            elif t == "target_priority_ensure":
                if is_strategy_action and last_state not in allowed_states:
                    log_mission(f"[EXEC] Skip target_priority_ensure while state={last_state}", "DEBUG")
                    continue
                expected_order = act.get("order")
                attachment_context = bool(
                    ctx is not None
                    and (
                        ctx.data.get("startup_gates_deferred") is True
                        or ctx.data.get("manual_return_reconciliation_active")
                        is True
                    )
                )
                save_coordinator = (
                    ctx.data.get(
                        "player_save_attachment_evidence"
                        if attachment_context
                        else "player_save_preflight_coordinator"
                    )
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
                    invalidate_snapshot = getattr(
                        save_coordinator,
                        "invalidate",
                        None,
                    )
                    if callable(invalidate_snapshot):
                        invalidate_snapshot(
                            "target_priority_repair_started",
                            check_ids=("target_priority",),
                        )
                        return
                    close_mapping_window = getattr(
                        save_coordinator,
                        "close_mapping_candidate_window",
                        None,
                    )
                    if callable(close_mapping_window):
                        close_mapping_window("target_priority_repair_started")

                record_ui_verification = getattr(
                    save_coordinator,
                    "record_ui_verification",
                    None,
                )
                record_mapping_observation = getattr(
                    save_coordinator,
                    "record_mapping_observation",
                    None,
                )

                def observe_initial_target_priority(
                    actual: Iterable[str],
                ) -> None:
                    values = [str(value) for value in actual]
                    if callable(record_mapping_observation):
                        try:
                            record_mapping_observation(
                                "target_priority",
                                build_mapping_candidate_ui_evidence(
                                    "target_priority",
                                    canonical_values=values,
                                    locator_values={
                                        f"rank:{index}": value
                                        for index, value in enumerate(values)
                                    },
                                ),
                            )
                        except Exception as exc:
                            log(
                                "[PLAYER_SAVE_MAPPING] Initial Target Priority "
                                f"observation failed: {exc}",
                                "DEBUG",
                            )
                if callable(record_ui_verification) or callable(
                    getattr(save_coordinator, "invalidate", None)
                ):
                    target_kwargs["repair_observer_fn"] = observe_target_repair
                if callable(record_mapping_observation):
                    target_kwargs["initial_evidence_observer_fn"] = (
                        observe_initial_target_priority
                    )
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
                    fallback = getattr(save_coordinator, "fallback_checks", None)
                    if callable(fallback):
                        fallback(
                            "target_priority_action_requirement_changed",
                            check_ids=("target_priority",),
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
                attachment_context = bool(
                    ctx is not None
                    and (
                        ctx.data.get("startup_gates_deferred") is True
                        or ctx.data.get("manual_return_reconciliation_active")
                        is True
                    )
                )
                save_coordinator = (
                    ctx.data.get(
                        "player_save_attachment_evidence"
                        if attachment_context
                        else "player_save_preflight_coordinator"
                    )
                    if ctx is not None
                    else None
                )
                if expected_order is None:
                    observation = observe_target_priority_order()
                else:
                    observation = observe_target_priority_order(
                        expected=expected_order
                    )
                record_mapping_observation = getattr(
                    save_coordinator,
                    "record_mapping_observation",
                    None,
                )
                if observation.observed and callable(record_mapping_observation):
                    values = list(observation.actual)
                    try:
                        record_mapping_observation(
                            "target_priority",
                            build_mapping_candidate_ui_evidence(
                                "target_priority",
                                canonical_values=values,
                                locator_values={
                                    f"rank:{index}": value
                                    for index, value in enumerate(values)
                                },
                            ),
                        )
                    except Exception as exc:
                        log(
                            "[PLAYER_SAVE_MAPPING] Target Priority observation "
                            f"failed: {exc}",
                            "DEBUG",
                        )
                if mv is not None:
                    mv["target_priority_observed"] = True
                    mv["target_priority_observation"] = observation.as_dict()
                    if attachment_validation:
                        matched = bool(
                            observation.observed
                            and (
                                observation.matches is True
                                or expected_order is None
                            )
                        )
                        _record_attached_observation_result(
                            mv,
                            check_id="target_priority",
                            matched=matched,
                            reason=(
                                "the observed Target Priority did not match"
                                if observation.observed
                                else "Target Priority could not be observed"
                            ),
                        )
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
                    reason = "session preflight profile requirements are missing"
                    _complete_session_preflight_degraded(
                        mv,
                        reason=reason,
                        failed_checks=("session_preflight",),
                    )
                    log_mission(
                        "[SESSION_PREFLIGHT] Missing profile requirements; "
                        "Automation continues degraded",
                        "WARN",
                    )
                    continue
                validator = str(act.get("validator") or "farm").strip().lower()
                if validator not in {"farm", "tournament"}:
                    reason = f"unsupported session preflight validator {validator!r}"
                    _complete_session_preflight_degraded(
                        mv,
                        reason=reason,
                        failed_checks=("session_preflight",),
                    )
                    log_mission(
                        "[SESSION_PREFLIGHT] Unsupported validator "
                        f"{validator!r}; Automation continues degraded",
                        "WARN",
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
                attached_context = bool(
                    ctx.data.get("startup_gates_deferred") is True
                )
                manual_return_context = bool(
                    ctx.data.get("manual_return_reconciliation_active") is True
                )
                # A process attachment never owns a current-battle teardown or
                # Home repair.  Its save-first pass stays in the battle for
                # every strategy; the action flag remains a source-level
                # declaration rather than an authority grant.
                attached_route = bool(attached_context or manual_return_context)
                if mv is not None:
                    save_coordinator = (
                        ctx.data.get("player_save_attachment_evidence")
                        if attached_context or manual_return_context
                        else ctx.data.get("player_save_preflight_coordinator")
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
                if attached_route:
                    preflight_kwargs["stay_in_battle"] = True
                if attached_route or "allow_repair" in act:
                    preflight_kwargs["allow_repair"] = bool(
                        act.get("allow_repair", True)
                    ) and not attached_route
                try:
                    if validator == "tournament":
                        result = run_read_only_gc_preflight(
                            effective_requirements,
                            validate_fn=(
                                validate_tournament_session_preflight_screens
                            ),
                            **preflight_kwargs,
                        )
                    else:
                        result = run_read_only_gc_preflight(
                            effective_requirements,
                            **preflight_kwargs,
                        )
                except Exception as exc:
                    reason = f"session preflight validator failed: {exc}"
                    _complete_session_preflight_degraded(
                        mv,
                        reason=reason,
                        failed_checks=("session_preflight",),
                    )
                    log_mission(
                        "[SESSION_PREFLIGHT] Validator failed; Automation "
                        f"continues degraded: {exc}",
                        "WARN",
                    )
                    continue
                evidence_payload = (
                    result.evidence.as_dict()
                    if result.evidence is not None
                    else {}
                )
                previous_evidence_payload = (
                    mv.get("gc_session_preflight_evidence")
                    if mv is not None
                    else None
                )
                if mv is not None:
                    mv["gc_session_preflight_last_status"] = result.status.value
                    mv["gc_session_preflight_last_reason"] = result.reason
                    mv["gc_session_preflight_evidence"] = evidence_payload
                if result.status is GcPreflightNavigationStatus.COMPLETE:
                    reported_mismatches = evidence_payload.get(
                        "reported_attachment_mismatches"
                    )
                    deferred_checks = evidence_payload.get("deferred_checks")
                    attachment_degraded = bool(
                        attached_route
                        and (
                            (
                                isinstance(reported_mismatches, Mapping)
                                and reported_mismatches
                            )
                            or (
                                isinstance(deferred_checks, (list, tuple))
                                and deferred_checks
                            )
                        )
                    )
                    attachment_failed_checks: set[str] = set()
                    if isinstance(reported_mismatches, Mapping):
                        attachment_failed_checks.update(
                            str(check_id) for check_id in reported_mismatches
                        )
                    if isinstance(deferred_checks, (list, tuple)):
                        attachment_failed_checks.update(
                            str(check_id) for check_id in deferred_checks
                        )
                    existing_degradation = (
                        mv.get("gc_running_configuration_degradation")
                        if mv is not None
                        else None
                    )
                    degradation_sources: set[str] = set()
                    if isinstance(existing_degradation, Mapping):
                        source = str(
                            existing_degradation.get("source") or ""
                        ).strip()
                        if source:
                            degradation_sources.add(source)
                        raw_sources = existing_degradation.get("sources", ())
                        if isinstance(
                            raw_sources,
                            (list, tuple, set, frozenset),
                        ):
                            degradation_sources.update(
                                str(value).strip()
                                for value in raw_sources
                                if str(value).strip()
                            )
                    retained_attachment_observation = bool(
                        attached_route
                        and "attachment_observation" in degradation_sources
                    )
                    if retained_attachment_observation:
                        attachment_degraded = True
                        raw_existing_checks = existing_degradation.get(
                            "failed_checks",
                            (),
                        )
                        if isinstance(
                            raw_existing_checks,
                            (list, tuple, set, frozenset),
                        ):
                            attachment_failed_checks.update(
                                str(check_id)
                                for check_id in raw_existing_checks
                                if str(check_id).strip()
                            )
                        if isinstance(previous_evidence_payload, Mapping):
                            attached_controls = previous_evidence_payload.get(
                                "attached_control_checks"
                            )
                            if isinstance(attached_controls, Mapping):
                                evidence_payload[
                                    "attached_control_checks"
                                ] = copy.deepcopy(dict(attached_controls))
                        evidence_payload.update(
                            {
                                "valid": False,
                                "degraded": True,
                                "disposition": "continue_degraded",
                            }
                        )
                    sorted_attachment_failed_checks = sorted(
                        attachment_failed_checks
                    )
                    if sorted_attachment_failed_checks:
                        evidence_payload["failed_checks"] = (
                            sorted_attachment_failed_checks
                        )
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = True
                        mv["gc_session_preflight_degraded"] = (
                            attachment_degraded
                        )
                        mv["gc_session_preflight_disposition"] = (
                            "continue_degraded"
                            if attachment_degraded
                            else "verified"
                        )
                        mv["gc_session_preflight_failed_checks"] = (
                            sorted_attachment_failed_checks
                        )
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = False
                        mv["gc_no_battle_setup_degraded"] = False
                        mv["gc_no_battle_setup_failure"] = {}
                        if not attachment_degraded:
                            existing_source = str(
                                (
                                    existing_degradation.get("source")
                                    if isinstance(
                                        existing_degradation,
                                        Mapping,
                                    )
                                    else ""
                                )
                                or ""
                            ).strip()
                            if existing_source not in {
                                "attachment_applicability",
                                "attachment_observation",
                            }:
                                mv.pop(
                                    "gc_running_configuration_degradation",
                                    None,
                                )
                                mv.pop("gc_degraded_home_repair", None)
                        _reset_repair_mismatch_attempts(mv)
                    variation_summary = summarize_gc_preflight_variations(
                        evidence_payload
                    )
                    completion = "[SESSION_PREFLIGHT] Session validation completed"
                    if variation_summary:
                        variation_kind = (
                            "immutable attachment mismatch reported"
                            if evidence_payload.get(
                                "reported_attachment_mismatches"
                            )
                            else "module variation observed"
                        )
                        completion += (
                            f"; {variation_kind} — " + variation_summary
                        )
                    if attachment_degraded:
                        deferred_only = bool(
                            not reported_mismatches
                            and attached_route
                            and isinstance(deferred_checks, (list, tuple))
                            and deferred_checks
                        )
                        completion = (
                            "[SESSION_PREFLIGHT] Attachment validation could not "
                            "verify Home-only checks; Automation continues "
                            "degraded and verification/repair is deferred to Home"
                            if deferred_only
                            else "[SESSION_PREFLIGHT] Attachment validation flagged "
                            "configuration gaps; Automation continues degraded "
                            "and repair is deferred to Home"
                        )
                    log_mission(
                        completion,
                        "WARN" if attachment_degraded else "INFO",
                    )
                    log_mission(
                        "[SESSION_PREFLIGHT] completed_evidence="
                        + json.dumps(evidence_payload, sort_keys=True),
                        "DEBUG",
                    )
                elif result.status is GcPreflightNavigationStatus.MISMATCH:
                    decision = decide_runtime_failure(
                        RuntimeFailureKind.CONFIGURATION_MISMATCH
                    )
                    failed_checks = list(
                        getattr(result.evidence, "failed_checks", ())
                    )
                    mismatch_summary = summarize_gc_preflight_mismatch(
                        evidence_payload
                    )
                    if mv is not None:
                        # A conclusive mismatch is still a completed check.  It
                        # cannot globally halt strategy or lifecycle input.
                        mv["gc_session_preflight_completed"] = True
                        mv["gc_session_preflight_degraded"] = True
                        mv["gc_session_preflight_disposition"] = (
                            decision.disposition.value
                        )
                        mv["gc_session_preflight_blocked"] = False
                        mv["gc_session_preflight_failed_checks"] = failed_checks
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = False
                        _reset_repair_mismatch_attempts(mv)
                    log_mission(
                        "[SESSION_PREFLIGHT] Configuration mismatch flagged — "
                        f"{mismatch_summary}. Automation continues in degraded "
                        "mode; only a safe Home boundary may repair it.",
                        "WARN",
                    )
                    log_mission(
                        "[SESSION_PREFLIGHT] mismatch_evidence="
                        + json.dumps(evidence_payload, sort_keys=True),
                        "DEBUG",
                    )
                else:
                    battle_ended = (
                        result.status
                        is GcPreflightNavigationStatus.BATTLE_ENDED
                    )
                    decision = decide_runtime_failure(
                        RuntimeFailureKind.VALIDATION_UNAVAILABLE
                    )
                    if mv is not None:
                        mv["gc_session_preflight_completed"] = not battle_ended
                        mv["gc_session_preflight_attempted"] = not battle_ended
                        mv["gc_session_preflight_degraded"] = not battle_ended
                        mv["gc_session_preflight_disposition"] = (
                            "battle_ended"
                            if battle_ended
                            else decision.disposition.value
                        )
                        mv["gc_session_preflight_blocked"] = False
                        mv["gc_session_preflight_repair_required"] = False
                        mv["gc_session_preflight_repair_in_progress"] = False
                        mv["gc_session_preflight_restart_available"] = False
                        mv["gc_session_preflight_failed_checks"] = []
                        _reset_repair_mismatch_attempts(mv)
                    interrupted_level = "INFO" if battle_ended else "WARN"
                    log_mission(
                        f"[SESSION_PREFLIGHT] Validation interrupted "
                        f"status={result.status.value} reason={result.reason}; "
                        + (
                            "the battle ended before another action"
                            if battle_ended
                            else "automation continues in degraded mode"
                        ),
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
            if attachment_validation and mv is not None:
                _record_attached_rule_disposition(
                    mv,
                    rule_id=attachment_rule_id,
                    disposition="observed",
                    action=str(t or "unknown"),
                )
        except Exception as e:
            if mv is not None and t in {
                "gc_session_preflight",
                "session_preflight",
            }:
                _complete_session_preflight_degraded(
                    mv,
                    reason=f"session preflight failed unexpectedly: {e}",
                    failed_checks=("session_preflight",),
                )
                log(
                    "[EXEC] Session preflight failed unexpectedly; Automation "
                    f"continues degraded: {e}",
                    "WARN",
                )
                continue
            if (
                attachment_context
                and mv is not None
                and (
                    t in {
                        "damage_slider_configure",
                        "orb_distance_configure",
                    }
                    or (
                        attachment_validation
                        and t == "target_priority_observe"
                    )
                )
            ):
                check_id = {
                    "damage_slider_configure": "damage_slider",
                    "orb_distance_configure": "orb_distance",
                    "target_priority_observe": "target_priority",
                }[str(t)]
                _record_attached_observation_result(
                    mv,
                    check_id=check_id,
                    matched=False,
                    reason=f"observer failed: {e}",
                )
                log(
                    f"[EXEC] Attached {check_id} observation failed; "
                    f"Automation continues degraded: {e}",
                    "WARN",
                )
                if attachment_validation:
                    _record_attached_rule_disposition(
                        mv,
                        rule_id=attachment_rule_id,
                        disposition="observer_failed_degraded",
                        action=str(t),
                    )
                continue
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
