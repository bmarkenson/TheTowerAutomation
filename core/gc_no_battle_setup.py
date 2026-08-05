"""Guarded GC preset correction at a verified no-battle Home boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Callable, Mapping, Sequence

from core.card_recharge_modes import (
    CardRechargeModesResult,
    ensure_card_recharge_modes,
    normalize_card_recharge_modes,
)
from core.battle_lifecycle import HomeBattleControl
from core.damage_adjuster import normalize_damage_percentage
from core.free_upgrade_locks import (
    inspect_free_upgrade_locks,
    normalize_free_upgrade_lock_requirements,
    select_workshop_upgrade_menu,
)
from core.home_battle import detect_home_battle_control
from core.home_perk_configuration import (
    HomePerkConfigurationResult,
    ensure_home_perk_configuration,
)
from core.gc_module_loadout import (
    evaluate_gc_module_loadout,
    ensure_gc_module_loadout,
    gc_module_loadout_evidence_from_assignments,
    normalize_gc_module_requirements,
)
from core.gc_preflight import GC_SECTION_SPECS, validate_gc_preflight_screens
from core.input import (
    TapVerification,
    safe_long_press,
    safe_tap,
    swipe_now,
    tap_if_visible,
)
from core.perk_configuration import (
    normalize_perk_configuration_requirements,
    normalize_perk_first_choice_requirement,
    perk_configuration_label,
)
from core.player_save import SAVE_ACCEPTED_DISPOSITIONS
from core.poison_swamp_stun import (
    PoisonSwampStunResult,
    ensure_poison_swamp_stun,
)
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.target_priority_config import validate_target_priority_order
from core.tournament_preflight import TOURNAMENT_SECTION_SPECS
from core.upgrade_navigation import swipe_upgrade_menu
from core.workshop_preset import (
    BOTS_AMPLIFY_PRESET_SLOT,
    BOTS_FARM_PRESET_SLOT,
    CARDS_FARM_PRESET_SLOT,
    CARDS_TOURNAMENT_PRESET_SLOT,
    FARM_PRESET_SLOT,
    TOURNEY_PRESET_SLOT,
    measure_preset_slot_selection,
)
from utils.logger import log, log_action_intent, log_result
from utils.ocr_utils import ocr_text_and_conf


NOT_ENOUGH_MEDALS_REGION = (100, 650, 880, 550)
NOT_ENOUGH_MEDALS_OK = (540, 1100)
GUARDIAN_INVENTORY_SETTLE_SECONDS = 1.0


@dataclass(frozen=True)
class _PresetSpec:
    secondary: str
    label: str
    region: tuple[int, int, int, int]


_PRESET_SPECS = {
    ("cards_deck", "Farm"): _PresetSpec(
        "CARDS_FARM_SLOT",
        "indicators.cards:farm_slot",
        CARDS_FARM_PRESET_SLOT,
    ),
    ("cards_deck", "Tournament"): _PresetSpec(
        "CARDS_TOURNAMENT_SLOT",
        "indicators.cards:tournament_slot",
        CARDS_TOURNAMENT_PRESET_SLOT,
    ),
    ("workshop_preset", "Farm"): _PresetSpec(
        "WORKSHOP_FARM_SLOT",
        "indicators.workshop:farm_slot",
        FARM_PRESET_SLOT,
    ),
    ("workshop_preset", "Tourney"): _PresetSpec(
        "WORKSHOP_TOURNEY_SLOT",
        "indicators.workshop:tourney_slot",
        TOURNEY_PRESET_SLOT,
    ),
    ("bots_preset", "Farm"): _PresetSpec(
        "BOTS_FARM_SLOT",
        "indicators.bots:farm_slot",
        BOTS_FARM_PRESET_SLOT,
    ),
    ("bots_preset", "Amplify"): _PresetSpec(
        "BOTS_AMPLIFY_SLOT",
        "indicators.bots:amplify_slot",
        BOTS_AMPLIFY_PRESET_SLOT,
    ),
}

_SUPPORTED_HOME_CONFIGURATIONS = {
    ("Farm", "Farm", "Farm"): GC_SECTION_SPECS,
    ("Tournament", "Tourney", "Amplify"): TOURNAMENT_SECTION_SPECS,
}

_HOME_PREFLIGHT_LABELS = {
    "home_boundary": "Home boundary",
    "cards_deck": "Cards deck",
    "card_recharge_modes": "Card recharge modes",
    "workshop_preset": "Workshop preset",
    "free_upgrade_locks": "Free Upgrade locks",
    "ultimate_weapons": "Ultimate Weapons",
    "bots_preset": "Bot preset",
    "guardian_chips": "Guardian Chips",
    "modules": "Modules",
    "target_priority": "Target Priority",
    "damage_slider": "Damage Slider",
    "auto_pick_perks": "Auto Pick Perks",
    "perk_configuration": "Perk configuration",
    "perk_first_choice": "First Perk Choice",
    "perk_bans": "Perk Bans",
    "perk_auto_pick_order": "Auto Pick priority",
}


class GcNoBattleSetupStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class GcNoBattleSetupResult:
    status: GcNoBattleSetupStatus
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    failed_check: str | None = None

    @property
    def complete(self) -> bool:
        return self.status is GcNoBattleSetupStatus.COMPLETE

    @property
    def interrupted(self) -> bool:
        return self.status is GcNoBattleSetupStatus.INTERRUPTED


def _finish_gc_no_battle_setup(
    result: GcNoBattleSetupResult,
    *,
    repairs: Sequence[str] = (),
) -> GcNoBattleSetupResult:
    """Emit the terminal result for one Home-only GC setup workflow."""

    repair_summary = "; ".join(str(repair) for repair in repairs)
    if result.status is GcNoBattleSetupStatus.COMPLETE:
        if repair_summary:
            summary = (
                "Home-only run configuration complete — verified; repairs "
                f"applied: {repair_summary}"
            )
        else:
            summary = (
                "Home-only run configuration complete — verified without "
                "changes"
            )
    elif result.status is GcNoBattleSetupStatus.INTERRUPTED:
        summary = (
            "Home-only run configuration interrupted — control changed during "
            f"{_HOME_PREFLIGHT_LABELS.get(result.failed_check or '', result.failed_check)}"
        )
    else:
        summary = f"Home-only run configuration failed — {result.reason}"
    if (
        repair_summary
        and result.status is not GcNoBattleSetupStatus.COMPLETE
    ):
        summary = f"{summary}; completed repairs: {repair_summary}"
    log_result(
        summary,
        detail=(
            f"[GC_NO_BATTLE] result={result.status.value} "
            f"failed_check={result.failed_check} reason={result.reason} "
            f"evidence_keys={sorted(result.evidence)} "
            f"repairs={list(repairs)}"
        ),
    )
    return result


class _SetupFailure(RuntimeError):
    pass


class _SetupControlInterrupted(RuntimeError):
    pass


def run_gc_no_battle_setup(
    requirements: Mapping[str, Any],
    *,
    screenshot=None,
    waivers: Mapping[str, Any] | None = None,
    save_decisions: Mapping[str, Mapping[str, Any]] | None = None,
    snapshot_invalidation_fn: Callable[[str], None] | None = None,
    capture_fn: Callable[[], Any] = capture_adb_screenshot,
    detector: Callable[[Any], Mapping[str, Any]] = detect_state_and_overlays,
    detect_home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    swipe_fn: Callable[[str], bool] = swipe_now,
    workshop_swipe_fn: Callable[[str, str], None] = swipe_upgrade_menu,
    measure_selection_fn: Callable[..., Any] = measure_preset_slot_selection,
    ensure_modules_fn: Callable[..., Any] = ensure_gc_module_loadout,
    evaluate_modules_fn: Callable[..., Any] = evaluate_gc_module_loadout,
    ensure_free_upgrade_locks_fn: Callable[..., Any] = inspect_free_upgrade_locks,
    select_workshop_menu_fn: Callable[..., Any] = select_workshop_upgrade_menu,
    ensure_poison_swamp_stun_fn: Callable[
        ..., PoisonSwampStunResult
    ] = ensure_poison_swamp_stun,
    ensure_perk_configuration_fn: Callable[
        ..., HomePerkConfigurationResult
    ] = ensure_home_perk_configuration,
    ensure_card_recharge_modes_fn: Callable[
        ..., CardRechargeModesResult
    ] = ensure_card_recharge_modes,
    validate_configuration_fn: Callable[..., Any] = validate_gc_preflight_screens,
    safe_long_press_fn: Callable[..., bool] = safe_long_press,
    action_guard_fn: Callable[[], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GcNoBattleSetupResult:
    """Correct supported persistent profile settings before a battle starts."""

    unsupported = _unsupported_requirement(requirements)
    if unsupported:
        _log_home_preflight_failure(
            "home_boundary",
            "supported profile configuration",
            unsupported,
        )
        return GcNoBattleSetupResult(
            GcNoBattleSetupStatus.UNSUPPORTED,
            unsupported,
        )
    logged_save_acceptances = sorted(
        str(check_id)
        for check_id, decision in (save_decisions or {}).items()
        if isinstance(decision, Mapping)
        and decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
    )
    log_action_intent(
        "Verifying Home-only run configuration",
        reason=(
            "check strategy-owned persistent settings before restart and "
            "repair only authoritative mismatches"
        ),
        detail=(
            f"[GC_NO_BATTLE] requirements={sorted(requirements)} "
            f"waivers={sorted((waivers or {}).keys())} "
            f"save_acceptances={logged_save_acceptances}"
        ),
    )

    original_safe_tap_fn = safe_tap_fn
    original_safe_long_press_fn = safe_long_press_fn
    original_tap_visible_fn = tap_visible_fn
    original_swipe_fn = swipe_fn
    original_workshop_swipe_fn = workshop_swipe_fn

    def require_action() -> None:
        if action_guard_fn is None:
            return
        control_blocked = False
        while not action_guard_fn():
            if not control_blocked:
                log(
                    "[GC_NO_BATTLE] Pause/stop blocked Home setup input; "
                    "waiting without cleanup actions",
                    "INFO",
                    console=True,
                )
            control_blocked = True
            sleep_fn(0.25)
        if control_blocked:
            raise _SetupControlInterrupted(
                "Home setup input was interrupted by persistent control"
            )

    def guarded_safe_tap(*args, **kwargs):
        require_action()
        return original_safe_tap_fn(*args, **kwargs)

    def guarded_safe_long_press(*args, **kwargs):
        require_action()
        return original_safe_long_press_fn(*args, **kwargs)

    def guarded_visible_tap(*args, **kwargs):
        require_action()
        return original_tap_visible_fn(*args, **kwargs)

    def guarded_swipe(*args, **kwargs):
        require_action()
        return original_swipe_fn(*args, **kwargs)

    def guarded_workshop_swipe(*args, **kwargs):
        require_action()
        return original_workshop_swipe_fn(*args, **kwargs)

    safe_tap_fn = guarded_safe_tap
    safe_long_press_fn = guarded_safe_long_press
    tap_visible_fn = guarded_visible_tap
    swipe_fn = guarded_swipe
    workshop_swipe_fn = guarded_workshop_swipe

    module_mode = _module_policy(requirements)
    target_priority_mode = _target_priority_policy(requirements)
    active_waivers = dict(waivers or {})
    active_save_decisions = {
        str(check_id): dict(decision)
        for check_id, decision in (save_decisions or {}).items()
        if isinstance(decision, Mapping)
        and decision.get("disposition") in SAVE_ACCEPTED_DISPOSITIONS
    }
    repairs: list[str] = []
    snapshot_invalidated = False

    def save_accepted(check_id: str) -> bool:
        return check_id in active_save_decisions

    def resolved_without_ui(check_id: str) -> bool:
        return check_id in active_waivers or save_accepted(check_id)

    def save_evidence(check_id: str) -> dict[str, Any]:
        decision = active_save_decisions[check_id]
        return {
            "status": str(decision.get("disposition") or "save_match"),
            "source": "player_save_preflight",
            "mapping_id": decision.get("mapping_id"),
            "disposition": decision.get("disposition"),
            "checked": False,
            "required": decision.get("expected"),
            "observed": decision.get("observed"),
            "reason": decision.get("reason"),
            "save_evidence_complete": decision.get(
                "save_evidence_complete"
            ),
            "save_requirement_supported": decision.get(
                "save_requirement_supported"
            ),
            "diagnostics": dict(decision.get("diagnostics") or {}),
        }

    def record_repair(description: str) -> None:
        nonlocal snapshot_invalidated
        repairs.append(description)
        if active_save_decisions and not snapshot_invalidated:
            active_save_decisions.clear()
            snapshot_invalidated = True
            if snapshot_invalidation_fn is not None:
                snapshot_invalidation_fn("home_ui_repair")
            log(
                "[PLAYER_SAVE_PREFLIGHT] First Home repair invalidated all "
                "remaining pre-action save decisions",
                "INFO",
                console=True,
            )
        log(
            f"[HOME_PREFLIGHT] Repair completed; {description}",
            "INFO",
            console=True,
        )

    evidence: dict[str, Any] = {
        "loadout_policies": {
            "modules": module_mode,
            "target_priority": target_priority_mode,
        },
        "waivers": active_waivers,
        "save_preflight": {
            "accepted_checks": sorted(active_save_decisions),
            "invalidated": False,
        },
    }
    current = screenshot if screenshot is not None else capture_fn()
    section_specs = _configuration_section_specs(requirements)
    accepted_sections: dict[str, Mapping[str, Any]] = {}
    current_check = "home_boundary"

    def log_check(check_id: str) -> None:
        if check_id in evidence:
            _log_home_preflight_evidence(
                check_id,
                requirements.get(check_id),
                evidence[check_id],
            )

    try:
        _require_no_battle_home(current, detector, detect_home_control_fn)

        card_recharge_requirements = requirements.get("card_recharge_modes")
        card_fields = ["cards_deck"]
        if "card_recharge_modes" in requirements:
            card_fields.append("card_recharge_modes")
        cards_configuration = None
        if all(resolved_without_ui(check_id) for check_id in card_fields):
            for check_id in card_fields:
                evidence[check_id] = (
                    _waived_evidence(
                        check_id,
                        requirements.get(check_id),
                        active_waivers[check_id],
                    )
                    if check_id in active_waivers
                    else save_evidence(check_id)
                )
                log_check(check_id)
            if save_accepted("cards_deck"):
                accepted_sections["cards"] = active_save_decisions["cards_deck"]
        else:
            current_check = "cards_deck"
            cards = _open_static(
                current,
                "navigation.goto_cards_home",
                "HOME_SCREEN",
                "CARDS",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
            )
            if current_check in active_waivers:
                evidence[current_check] = _waived_evidence(
                    current_check,
                    requirements.get(current_check),
                    active_waivers[current_check],
                )
            elif save_accepted(current_check):
                evidence[current_check] = save_evidence(current_check)
            else:
                preset = _preset_spec(current_check, requirements)
                cards, preset_changed = _ensure_preset(
                    cards,
                    state="CARDS",
                    slot_secondary=preset.secondary,
                    slot_label=preset.label,
                    slot_region=preset.region,
                    capture_fn=capture_fn,
                    detector=detector,
                    tap_visible_fn=tap_visible_fn,
                    measure_selection_fn=measure_selection_fn,
                    sleep_fn=sleep_fn,
                )
                if preset_changed:
                    record_repair(
                        f"Cards deck selected {requirements[current_check]}"
                    )
                evidence[current_check] = requirements[current_check]
            log_check(current_check)
            cards_configuration = cards

            current_check = "card_recharge_modes"
            if current_check in active_waivers:
                evidence[current_check] = _waived_evidence(
                    current_check,
                    card_recharge_requirements,
                    active_waivers[current_check],
                )
            elif save_accepted(current_check):
                evidence[current_check] = save_evidence(current_check)
            elif card_recharge_requirements is not None:
                recharge_result = ensure_card_recharge_modes_fn(
                    card_recharge_requirements,
                    cards_screenshot=cards,
                    capture_fn=capture_fn,
                    detector=detector,
                    safe_long_press_fn=safe_long_press_fn,
                    safe_tap_fn=safe_tap_fn,
                    swipe_fn=swipe_fn,
                    sleep_fn=sleep_fn,
                )
                recharge_payload = recharge_result.as_dict()
                evidence[current_check] = recharge_payload
                cards = recharge_result.screenshot
                if not recharge_result.valid:
                    raise _SetupFailure(
                        "Card recharge modes remained invalid after correction"
                    )
                normalized_recharge_modes = normalize_card_recharge_modes(
                    card_recharge_requirements
                )
                for label in recharge_payload.get("changed_labels") or ():
                    required_mode = normalized_recharge_modes.get(str(label))
                    target = (
                        required_mode.value.replace("_", " ")
                        if required_mode is not None
                        else "the required mode"
                    )
                    record_repair(f"{label} recharge mode set to {target}")
            if card_recharge_requirements is not None:
                log_check(current_check)
            current = _return_home(
                cards,
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )

        if (
            "perk_first_choice" in requirements
            or "perk_bans" in requirements
            or "perk_auto_pick_order" in requirements
        ):
            perk_fields = tuple(
                check_id
                for check_id in (
                    "perk_first_choice",
                    "perk_bans",
                    "perk_auto_pick_order",
                )
                if check_id in requirements
            )
            if all(resolved_without_ui(check_id) for check_id in perk_fields):
                for check_id in perk_fields:
                    field_evidence = (
                        _waived_evidence(
                            check_id,
                            requirements.get(check_id),
                            active_waivers[check_id],
                        )
                        if check_id in active_waivers
                        else save_evidence(check_id)
                    )
                    evidence[check_id] = field_evidence
                    _log_home_preflight_evidence(
                        check_id,
                        requirements.get(check_id),
                        field_evidence,
                    )
            else:
                current_check = "perk_configuration"
                perk_result = ensure_perk_configuration_fn(
                    requirements,
                    home_screenshot=current,
                    capture_fn=capture_fn,
                    detector=detector,
                    detect_home_control_fn=detect_home_control_fn,
                    safe_tap_fn=safe_tap_fn,
                    tap_visible_fn=tap_visible_fn,
                    swipe_fn=swipe_fn,
                    measure_selection_fn=measure_selection_fn,
                    waived_fields=tuple(
                        check_id
                        for check_id in perk_fields
                        if check_id in active_waivers or save_accepted(check_id)
                    ),
                    sleep_fn=sleep_fn,
                    operator_workflow=False,
                )
                for check_id in perk_fields:
                    if check_id in active_waivers:
                        field_evidence = _waived_evidence(
                            check_id,
                            requirements.get(check_id),
                            active_waivers[check_id],
                        )
                    elif save_accepted(check_id):
                        field_evidence = save_evidence(check_id)
                    else:
                        field_evidence = dict(
                            perk_result.evidence[check_id]
                        )
                        field_evidence.setdefault(
                            "changed",
                            perk_result.changed,
                        )
                    evidence[check_id] = field_evidence
                    _log_home_preflight_evidence(
                        check_id,
                        requirements.get(check_id),
                        field_evidence,
                    )
                    if field_evidence.get("changed") is True:
                        record_repair(
                            {
                                "perk_first_choice": (
                                    "First Perk Choice restored"
                                ),
                                "perk_bans": "Ban Perks list restored",
                                "perk_auto_pick_order": (
                                    "Auto Pick priority restored"
                                ),
                            }[check_id]
                        )
                current = perk_result.home_screenshot
                if not perk_result.valid:
                    current_check = (
                        perk_result.failed_check or "perk_configuration"
                    )
                    raise _SetupFailure(perk_result.reason)

        home_stun_required = _home_poison_swamp_stun_requirement(requirements)
        workshop_fields = ["workshop_preset"]
        if "free_upgrade_locks" in requirements:
            workshop_fields.append("free_upgrade_locks")
        if home_stun_required is not None:
            workshop_fields.append("poison_swamp_stun")
        workshop_needed = not all(
            resolved_without_ui(check_id) for check_id in workshop_fields
        )

        current_check = "workshop_preset"
        workshop = (
            _open_static(
                current,
                "navigation.goto_workshop_home",
                "HOME_SCREEN",
                "WORKSHOP",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
            )
            if workshop_needed
            else None
        )
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
        elif save_accepted(current_check):
            evidence[current_check] = save_evidence(current_check)
            if workshop is None:
                accepted_sections["workshop"] = active_save_decisions[
                    current_check
                ]
        else:
            preset = _preset_spec(current_check, requirements)
            workshop, preset_changed = _ensure_preset(
                workshop,
                state="WORKSHOP",
                slot_secondary=preset.secondary,
                slot_label=preset.label,
                slot_region=preset.region,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                measure_selection_fn=measure_selection_fn,
                sleep_fn=sleep_fn,
            )
            if preset_changed:
                record_repair(
                    f"Workshop preset selected {requirements[current_check]}"
                )
            evidence[current_check] = requirements[current_check]
        log_check(current_check)
        workshop_configuration = workshop
        current_check = "free_upgrade_locks"
        free_upgrade_lock_requirements = requirements.get("free_upgrade_locks")
        if current_check in active_waivers:
            waived_locks = _waived_evidence(
                current_check,
                free_upgrade_lock_requirements,
                active_waivers[current_check],
            )
            waived_locks.update(
                boundary=HomeBattleControl.NEW_BATTLE.value,
                checked=False,
                valid=None,
            )
            evidence[current_check] = waived_locks
        elif save_accepted(current_check):
            lock_payload = save_evidence(current_check)
            lock_payload.update(
                boundary=HomeBattleControl.NEW_BATTLE.value,
                valid=True,
            )
            evidence[current_check] = lock_payload
        elif free_upgrade_lock_requirements is not None:
            lock_result = ensure_free_upgrade_locks_fn(
                free_upgrade_lock_requirements,
                screenshot=workshop,
                enforce=True,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                swipe_fn=workshop_swipe_fn,
                sleep_fn=sleep_fn,
            )
            normalized_lock_requirements = normalize_free_upgrade_lock_requirements(
                free_upgrade_lock_requirements,
                require_farm_set=True,
            )
            lock_evidence = lock_result.evidence
            lock_payload = lock_evidence.as_dict()
            lock_payload.update(
                boundary=HomeBattleControl.NEW_BATTLE.value,
                checked=True,
                required=list(normalized_lock_requirements),
                status=(
                    "verified"
                    if lock_evidence.valid
                    else "mismatch"
                    if lock_evidence.has_authoritative_mismatch
                    else "unavailable"
                ),
                changed_labels=list(
                    getattr(lock_result, "changed_labels", ()) or ()
                ),
            )
            evidence["free_upgrade_locks"] = lock_payload
            if not lock_result.evidence.valid:
                raise _SetupFailure(
                    "Free Upgrade locks remained invalid after correction"
                )
            changed_locks = lock_payload["changed_labels"]
            if changed_locks:
                record_repair(
                    "Free Upgrade locks enabled for "
                    + ", ".join(str(label) for label in changed_locks)
                )
            workshop = lock_result.screenshot
        log_check(current_check)

        current_check = "ultimate_weapons"
        if home_stun_required is not None and current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
        elif home_stun_required is not None and save_accepted("poison_swamp_stun"):
            stun_evidence = save_evidence("poison_swamp_stun")
            evidence[current_check] = {
                "boundary": HomeBattleControl.NEW_BATTLE.value,
                "checked": [],
                "observations": {
                    "Poison Swamp": {
                        "stun": stun_evidence.get("observed"),
                    },
                },
                "valid": True,
                "source": "player_save_preflight",
                "components": {"poison_swamp_stun": stun_evidence},
            }
        elif home_stun_required is not None:
            workshop = select_workshop_menu_fn(
                workshop,
                "ultimate weapons",
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
            )
            stun_result = ensure_poison_swamp_stun_fn(
                screenshot=workshop,
                required_state=home_stun_required,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                tap_visible_fn=tap_visible_fn,
                swipe_fn=workshop_swipe_fn,
                sleep_fn=sleep_fn,
            )
            workshop = stun_result.screenshot
            stun_state = stun_result.evidence.state.value
            evidence[current_check] = {
                "boundary": HomeBattleControl.NEW_BATTLE.value,
                "checked": ["Poison Swamp.stun"],
                "observations": {
                    "Poison Swamp": {"stun": stun_state},
                },
                "valid": stun_state == home_stun_required,
                "changed": stun_result.changed,
            }
            if stun_state != home_stun_required:
                raise _SetupFailure(
                    "Poison Swamp Stun remained "
                    f"{stun_state} after Home correction to "
                    f"{home_stun_required}"
                )
            if stun_result.changed:
                record_repair(
                    f"Poison Swamp Stun set to {home_stun_required}"
                )
        elif isinstance(requirements.get(current_check), Mapping):
            evidence[current_check] = {
                "boundary": HomeBattleControl.NEW_BATTLE.value,
                "checked": [],
                "observations": {},
                "valid": True,
                "reason": "no_supported_home_controls_required",
            }
        log_check(current_check)
        if workshop is not None:
            current = _return_home(
                workshop,
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )

        current_check = "bots_preset"
        bots_configuration = None
        if resolved_without_ui(current_check):
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            ) if current_check in active_waivers else save_evidence(current_check)
            if save_accepted(current_check):
                accepted_sections["bots"] = active_save_decisions[current_check]
        else:
            event = _open_visible(
                current,
                "navigation.home_event",
                "HOME_SCREEN",
                "EVENT",
                capture_fn,
                detector,
                tap_visible_fn,
                sleep_fn,
            )
            bots = _open_static(
                event,
                "navigation.event:bots_tab",
                "EVENT",
                "EVENT",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
            )
            bots = _ensure_event_bots_top(
                bots,
                capture_fn=capture_fn,
                detector=detector,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
            preset = _preset_spec(current_check, requirements)
            bots, preset_changed = _ensure_preset(
                bots,
                state="EVENT",
                slot_secondary=preset.secondary,
                slot_label=preset.label,
                slot_region=preset.region,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                measure_selection_fn=measure_selection_fn,
                sleep_fn=sleep_fn,
            )
            if preset_changed:
                record_repair(
                    f"Bot preset selected {requirements[current_check]}"
                )
            evidence[current_check] = requirements[current_check]
            bots_configuration = bots
            current = _return_home(
                bots,
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )
        log_check(current_check)

        current_check = "guardian_chips"
        guardians_configuration = None
        if resolved_without_ui(current_check):
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            ) if current_check in active_waivers else save_evidence(current_check)
            if save_accepted(current_check):
                accepted_sections["guardians"] = active_save_decisions[
                    current_check
                ]
        else:
            guild = _open_visible(
                current,
                "navigation.home_guild",
                "HOME_SCREEN",
                "GUILD",
                capture_fn,
                detector,
                tap_visible_fn,
                sleep_fn,
            )
            guardians = _open_static(
                guild,
                "navigation.guild:guardian_tab",
                "GUILD",
                "GUILD",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
                required_secondary="GUILD_GUARDIAN_SCREEN",
            )
            guardians, changed_guardians = _ensure_guardian_loadout(
                guardians,
                requirements[current_check],
                capture_fn,
                detector,
                tap_visible_fn,
                sleep_fn,
            )
            if changed_guardians:
                record_repair(
                    "Guardian Chips equipped "
                    + ", ".join(changed_guardians)
                )
            evidence[current_check] = list(requirements[current_check])
            guardians_configuration = guardians
            current = _return_home(
                guardians,
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )
        log_check(current_check)

        current_check = "modules"
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
        elif save_accepted(current_check):
            module_decision = active_save_decisions[current_check]
            observed_assignments = module_decision.get("observed")
            if not isinstance(observed_assignments, Mapping):
                raise _SetupFailure(
                    "save-backed module assignments were unavailable"
                )
            module_evidence = gc_module_loadout_evidence_from_assignments(
                requirements["modules"],
                observed_assignments,
            )
            if module_mode == "enforce" and not module_evidence.valid:
                raise _SetupFailure(
                    "save-backed module loadout did not match the requirement"
                )
            if module_mode == "observe" and not module_evidence.fully_observed:
                raise _SetupFailure(
                    "save-backed module loadout was not completely observed"
                )
            module_payload = module_evidence.as_dict()
            module_payload.update(
                mode=module_mode,
                checked=False,
                source="player_save_preflight",
                status=module_decision.get("disposition"),
                mapping_id=module_decision.get("mapping_id"),
                disposition=module_decision.get("disposition"),
                reason=module_decision.get("reason"),
                save_evidence_complete=module_decision.get(
                    "save_evidence_complete"
                ),
                save_requirement_supported=module_decision.get(
                    "save_requirement_supported"
                ),
                diagnostics=dict(module_decision.get("diagnostics") or {}),
            )
            evidence[current_check] = module_payload
        elif module_mode == "enforce":
            modules = _open_static(
                current,
                "navigation.goto_modules_home",
                "HOME_SCREEN",
                "MODULES",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
            )
            module_repairs: list[Any] = []

            def observe_module_repairs(slots: Sequence[Any]) -> None:
                module_repairs.extend(slots)

            module_evidence = ensure_modules_fn(
                requirements["modules"],
                screenshot=modules,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
                repair_observer_fn=observe_module_repairs,
            )
            if not module_evidence.valid:
                raise _SetupFailure(
                    "module loadout remained invalid after correction"
                )
            if module_repairs:
                assignments = ", ".join(
                    f"{slot.slot_key}={slot.expected}"
                    for slot in module_repairs
                )
                record_repair(f"Module loadout restored ({assignments})")
            module_payload = module_evidence.as_dict()
            module_payload.update(mode=module_mode, checked=True)
            evidence["modules"] = module_payload
            current = _return_home(
                capture_fn(),
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )
        elif module_mode == "observe":
            modules = _open_static(
                current,
                "navigation.goto_modules_home",
                "HOME_SCREEN",
                "MODULES",
                capture_fn,
                detector,
                safe_tap_fn,
                sleep_fn,
            )
            module_evidence = evaluate_modules_fn(
                modules,
                requirements["modules"],
            )
            module_payload = module_evidence.as_dict()
            module_payload.update(mode=module_mode, checked=True)
            evidence["modules"] = module_payload
            current = _return_home(
                modules,
                capture_fn,
                detector,
                detect_home_control_fn,
                safe_tap_fn,
                tap_visible_fn,
                sleep_fn,
            )
        else:
            evidence["modules"] = {
                "mode": module_mode,
                "checked": False,
            }
        log_check(current_check)

        current_check = "target_priority"
        target_priority_requirement = requirements.get("target_priority")
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                target_priority_requirement,
                active_waivers[current_check],
            )
        elif save_accepted(current_check):
            target_evidence = save_evidence(current_check)
            target_evidence.update(
                mode=target_priority_mode,
                boundary="RUNNING",
                valid=True,
            )
            evidence[current_check] = target_evidence
        elif target_priority_mode != "preserve":
            # Target Priority is exposed only from the in-battle side menu.
            # Keep its resolved policy/order in the generated runtime plan,
            # where the existing RUNNING-only action verifies it.
            evidence[current_check] = {
                "mode": target_priority_mode,
                "checked": False,
                "valid": None,
                "boundary": "RUNNING",
                "reason": "battle_only_control",
            }
        else:
            evidence[current_check] = {
                "mode": target_priority_mode,
                "checked": False,
            }
        log_check(current_check)

        current_check = "damage_slider"
        damage_slider = requirements.get("damage_slider")
        if damage_slider is not None:
            evidence[current_check] = {
                "mode": str(damage_slider["mode"]),
                "value": str(damage_slider["value"]),
                "checked": False,
                "valid": None,
                "boundary": "RUNNING",
                "reason": "battle_only_control",
            }
        log_check(current_check)

        if "auto_pick_perks" in requirements:
            auto_pick_evidence = (
                save_evidence("auto_pick_perks")
                if save_accepted("auto_pick_perks")
                else {
                    "checked": False,
                    "valid": None,
                    "boundary": "RUNNING",
                    "reason": "battle_only_control",
                }
            )
            _log_home_preflight_evidence(
                "auto_pick_perks",
                requirements["auto_pick_perks"],
                auto_pick_evidence,
            )
            evidence["auto_pick_perks"] = auto_pick_evidence

        configuration_kwargs = dict(
            cards_screen=cards_configuration,
            workshop_screen=workshop_configuration,
            bots_screen=bots_configuration,
            guardians_screen=guardians_configuration,
            detector=detector,
            section_specs=section_specs,
        )
        if accepted_sections:
            configuration_kwargs["accepted_sections"] = accepted_sections
        configuration = validate_configuration_fn(**configuration_kwargs)
        configuration_payload = configuration.as_dict()
        configuration_payload["save_backed_sections"] = {
            section: {
                "disposition": "save_match",
                "reason": decision.get("reason"),
            }
            for section, decision in sorted(accepted_sections.items())
        }
        section_checks = (
            ("cards", "cards_deck"),
            ("workshop", "workshop_preset"),
            ("bots", "bots_preset"),
            ("guardians", "guardian_chips"),
        )
        configuration_failures = [
            check_id
            for section, check_id in section_checks
            if check_id not in active_waivers
            and isinstance(configuration_payload.get(section), Mapping)
            and configuration_payload[section].get("valid") is False
        ]
        configuration_payload["blocking_valid"] = not configuration_failures
        evidence["configuration"] = configuration_payload
        if configuration_failures:
            if any(
                save_accepted(check_id) for check_id in configuration_failures
            ):
                active_save_decisions.clear()
                snapshot_invalidated = True
                if snapshot_invalidation_fn is not None:
                    snapshot_invalidation_fn("save_ui_contradiction")
            current_check = configuration_failures[0]
            raise _SetupFailure(
                "Home boundary configuration evidence contradicted the "
                "completed checks: "
                + ", ".join(configuration_failures)
            )
        evidence["save_preflight"]["invalidated"] = snapshot_invalidated
        evidence["save_preflight"]["remaining_checks"] = sorted(
            active_save_decisions
        )
    except _SetupControlInterrupted as exc:
        log(
            "[GC_NO_BATTLE] Home setup control interruption ended; "
            "restoring verified Home before a fresh retry",
            "INFO",
            console=True,
        )
        _recover_home(
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )
        return _finish_gc_no_battle_setup(
            GcNoBattleSetupResult(
                GcNoBattleSetupStatus.INTERRUPTED,
                str(exc),
                evidence,
                current_check,
            ),
            repairs=repairs,
        )
    except Exception as exc:
        _log_home_preflight_failure(
            current_check,
            requirements.get(current_check, "valid no-battle Home"),
            exc,
        )
        _recover_home(
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )
        log(f"[GC_NO_BATTLE] Setup failed: {exc}", "ERROR")
        return _finish_gc_no_battle_setup(
            GcNoBattleSetupResult(
                GcNoBattleSetupStatus.FAILED,
                str(exc),
                evidence,
                current_check,
            ),
            repairs=repairs,
        )

    log(
        "[GC_NO_BATTLE] Profile Home settings verified/corrected before Battle",
        "DEBUG",
    )
    return _finish_gc_no_battle_setup(
        GcNoBattleSetupResult(
            GcNoBattleSetupStatus.COMPLETE,
            "supported no-battle requirements verified",
            evidence,
        ),
        repairs=repairs,
    )


def _log_home_preflight_evidence(
    check_id: str,
    expected: object,
    check_evidence: object,
) -> None:
    expected_value = expected
    disposition = "passed"
    level = "INFO"
    observed: object = check_evidence

    if isinstance(check_evidence, Mapping):
        status = str(check_evidence.get("status") or "").strip().lower()
        reason = str(check_evidence.get("reason") or "").strip()
        if status == "waived":
            waiver = check_evidence.get("waiver")
            if isinstance(waiver, Mapping):
                observed = (
                    waiver.get("label")
                    or waiver.get("value")
                    or waiver.get("reason")
                    or "configured one-run waiver"
                )
            else:
                observed = waiver or "configured one-run waiver"
            disposition = "waived"
            level = "WARN"
        elif reason in {
            "battle_only_control",
            "no_supported_home_controls_required",
        }:
            observed = (
                "in-battle control pending"
                if check_id in {"target_priority", "damage_slider"}
                else "in-battle validation pending"
            )
            disposition = "deferred"
        elif check_id == "free_upgrade_locks":
            required = check_evidence.get("required") or []
            if check_evidence.get("source") == "player_save_preflight":
                unmanaged = (
                    check_evidence.get("diagnostics", {}).get(
                        "unmanaged_locks",
                        [],
                    )
                    if isinstance(check_evidence.get("diagnostics"), Mapping)
                    else []
                )
                observed = f"{len(required)}/{len(required)} required locks verified"
                if unmanaged:
                    observed += "; unmanaged locks: " + ", ".join(
                        str(value) for value in unmanaged
                    )
                disposition = "passed"
                level = "INFO"
                locks = []
            else:
                locks = check_evidence.get("locks") or []
            valid_count = sum(
                bool(lock.get("valid"))
                for lock in locks
                if isinstance(lock, Mapping)
            )
            if check_evidence.get("source") != "player_save_preflight":
                observed = f"{valid_count}/{len(required)} required locks verified"
            if check_evidence.get("valid") is False:
                disposition = "failed"
                level = "ERROR"
        elif check_id == "ultimate_weapons":
            poison_swamp = (
                check_evidence.get("observations", {}).get("Poison Swamp", {})
                if isinstance(check_evidence.get("observations"), Mapping)
                else {}
            )
            observed_stun = (
                poison_swamp.get("stun")
                if isinstance(poison_swamp, Mapping)
                else None
            )
            observed = (
                f"Poison Swamp Stun {observed_stun}"
                if observed_stun
                else "Home-supported subset verified"
            )
            if check_evidence.get("changed"):
                observed = f"{observed} after correction"
            disposition = "Home subset passed"
        elif check_id == "modules":
            mode = str(check_evidence.get("mode") or "enforce")
            if expected_value is None and mode == "preserve":
                expected_value = "preserve current module loadout"
            slots = check_evidence.get("slots") or []
            if not check_evidence.get("checked") and mode == "preserve":
                observed = "unchanged"
            elif slots:
                valid_count = sum(
                    bool(slot.get("valid"))
                    for slot in slots
                    if isinstance(slot, Mapping)
                )
                observed = (
                    f"{valid_count}/{len(slots)} configured assignments matched"
                )
                disposition = "observed" if mode == "observe" else "passed"
            else:
                observed = "module loadout observed"
                disposition = "observed" if mode == "observe" else "passed"
        elif check_id == "card_recharge_modes":
            if (
                check_evidence.get("source") == "player_save_preflight"
                and isinstance(check_evidence.get("observed"), Mapping)
            ):
                observed = check_evidence["observed"]
            else:
                modes = check_evidence.get("modes") or []
                observed = ", ".join(
                    f"{mode.get('label')}={mode.get('observed')}"
                    for mode in modes
                    if isinstance(mode, Mapping)
                ) or "unavailable"
            if check_evidence.get("changed"):
                observed = f"{observed} after correction"
            if check_evidence.get("valid") is False:
                disposition = "failed"
                level = "ERROR"
        elif check_id == "perk_first_choice":
            observed_choice = check_evidence.get("observed")
            observed = (
                perk_configuration_label(str(observed_choice))
                if isinstance(observed_choice, str)
                else "unavailable"
            )
            if check_evidence.get("changed"):
                observed = f"{observed} after correction"
            if check_evidence.get("valid") is False:
                disposition = "failed"
                level = "ERROR"
        elif check_id in {"perk_bans", "perk_auto_pick_order"}:
            expected_labels = check_evidence.get("expected_labels") or []
            observed_labels = check_evidence.get("observed_labels") or []
            if (
                check_evidence.get("source") == "player_save_preflight"
                and isinstance(check_evidence.get("observed"), (list, tuple))
            ):
                observed_labels = [
                    perk_configuration_label(str(item))
                    for item in check_evidence["observed"]
                ]
            if expected_labels:
                expected_value = " > ".join(
                    str(item) for item in expected_labels
                )
            observed = " > ".join(
                str(item) for item in observed_labels
            ) or "unavailable"
            if check_evidence.get("changed"):
                observed = f"{observed} after correction"
            if check_evidence.get("valid") is False:
                disposition = "failed"
                level = "ERROR"
        elif check_evidence.get("valid") is False:
            disposition = "failed"
            level = "ERROR"
        if (
            check_id == "target_priority"
            and expected_value is None
            and str(check_evidence.get("mode") or "") == "preserve"
        ):
            expected_value = "preserve current priority order"

    label = _HOME_PREFLIGHT_LABELS.get(
        check_id,
        str(check_id).replace("_", " ").title(),
    )
    save_detail = ""
    if (
        isinstance(check_evidence, Mapping)
        and check_evidence.get("source") == "player_save_preflight"
    ):
        save_detail = (
            "; source=player_save_preflight"
            f"; mapping={check_evidence.get('mapping_id') or 'none'}"
            "; complete="
            f"{bool(check_evidence.get('save_evidence_complete'))}"
            "; supported="
            f"{bool(check_evidence.get('save_requirement_supported'))}"
            f"; disposition={check_evidence.get('disposition') or 'save_match'}"
            f"; reason={check_evidence.get('reason') or 'unspecified'}"
        )
    log(
        f"[HOME_PREFLIGHT] {label} {disposition}; "
        f"expected={_home_preflight_value(check_id, expected_value)}; "
        f"observed={_home_preflight_value(check_id, observed)}"
        f"{save_detail}",
        level,
    )


def _log_home_preflight_failure(
    check_id: str,
    expected: object,
    reason: object,
) -> None:
    label = _HOME_PREFLIGHT_LABELS.get(
        check_id,
        str(check_id).replace("_", " ").title(),
    )
    log(
        f"[HOME_PREFLIGHT] {label} failed; "
        f"expected={_home_preflight_value(check_id, expected)}; "
        f"observed={reason}",
        "ERROR",
    )


def _home_preflight_value(check_id: str, value: object) -> str:
    if check_id == "modules" and isinstance(value, Mapping):
        return f"{len(value)} configured assignments"
    if check_id == "ultimate_weapons" and isinstance(value, Mapping):
        poison_swamp = value.get("Poison Swamp")
        if isinstance(poison_swamp, Mapping) and poison_swamp.get("stun"):
            return f"Poison Swamp Stun {poison_swamp['stun']}"
        return f"{len(value)} configured weapons"
    if check_id == "target_priority" and isinstance(value, (list, tuple)):
        return f"{len(value)} configured priorities"
    if check_id == "damage_slider" and isinstance(value, Mapping):
        return str(value.get("value") or value.get("mode") or value)
    if check_id == "card_recharge_modes" and isinstance(value, Mapping):
        return ", ".join(f"{key}={item}" for key, item in value.items())
    if check_id == "perk_first_choice" and isinstance(value, str):
        return perk_configuration_label(value)
    if check_id in {"perk_bans", "perk_auto_pick_order"} and isinstance(
        value,
        (list, tuple),
    ):
        return " > ".join(
            perk_configuration_label(str(item)) for item in value
        )
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={item}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    return str(value)


def _waived_evidence(
    check_id: str,
    required: object,
    waiver: object,
) -> dict[str, Any]:
    payload = dict(waiver) if isinstance(waiver, Mapping) else {"value": waiver}
    return {
        "status": "waived",
        "check_id": check_id,
        "required": required,
        "waiver": payload,
    }


def _unsupported_requirement(requirements: Mapping[str, Any]) -> str | None:
    configuration = _configuration_key(requirements)
    if configuration not in _SUPPORTED_HOME_CONFIGURATIONS:
        return (
            "unsupported Home preset combination "
            f"cards_deck={configuration[0]!r}, "
            f"workshop_preset={configuration[1]!r}, "
            f"bots_preset={configuration[2]!r}"
        )
    chips = {str(chip).strip() for chip in requirements.get("guardian_chips") or []}
    supported_chips = (
        {"Fetch", "Summon", "Scout"}
        if configuration == ("Farm", "Farm", "Farm")
        else {"Attack", "Ally", "Scout"}
    )
    if chips != supported_chips:
        return f"unsupported guardian_chips={sorted(chips)!r}"
    if "free_upgrade_locks" in requirements:
        try:
            normalize_free_upgrade_lock_requirements(
                requirements["free_upgrade_locks"],
                require_farm_set=True,
            )
        except ValueError as exc:
            return str(exc)
    try:
        module_mode = _module_policy(requirements)
    except ValueError as exc:
        return str(exc)
    if module_mode == "preserve":
        if "modules" in requirements:
            return "preserved modules must not supply module requirements"
    else:
        try:
            normalize_gc_module_requirements(requirements.get("modules"))
        except ValueError as exc:
            return str(exc)
    try:
        _target_priority_policy(requirements)
    except ValueError as exc:
        return str(exc)
    try:
        _home_poison_swamp_stun_requirement(requirements)
    except ValueError as exc:
        return str(exc)
    if (
        "perk_first_choice" in requirements
        or
        "perk_bans" in requirements
        or "perk_auto_pick_order" in requirements
    ):
        try:
            if "perk_first_choice" in requirements:
                normalize_perk_first_choice_requirement(requirements)
            normalize_perk_configuration_requirements(requirements)
        except ValueError as exc:
            return str(exc)
    if "card_recharge_modes" in requirements:
        try:
            normalize_card_recharge_modes(requirements["card_recharge_modes"])
        except ValueError as exc:
            return str(exc)
    damage_slider = requirements.get("damage_slider")
    if damage_slider is not None:
        if not isinstance(damage_slider, Mapping):
            return "damage_slider must be a mapping"
        if set(damage_slider) != {"mode", "value"}:
            return "damage_slider must define exactly mode and value"
        if str(damage_slider.get("mode") or "").strip().lower() != "enforce":
            return "Home-deferred damage_slider mode must be enforce"
        try:
            normalize_damage_percentage(damage_slider.get("value"))
        except ValueError as exc:
            return f"damage_slider {exc}"
    return None


def _configuration_key(requirements: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(
        str(requirements.get(key) or "").strip()
        for key in ("cards_deck", "workshop_preset", "bots_preset")
    )


def _configuration_section_specs(requirements: Mapping[str, Any]):
    return _SUPPORTED_HOME_CONFIGURATIONS[_configuration_key(requirements)]


def _preset_spec(check_id: str, requirements: Mapping[str, Any]) -> _PresetSpec:
    key = (check_id, str(requirements.get(check_id) or "").strip())
    try:
        return _PRESET_SPECS[key]
    except KeyError as exc:
        raise _SetupFailure(
            f"unsupported {check_id}={requirements.get(check_id)!r}"
        ) from exc


def _module_policy(requirements: Mapping[str, Any]) -> str:
    policies = requirements.get("loadout_policies") or {}
    if not isinstance(policies, Mapping):
        raise ValueError("loadout_policies must be a mapping")
    unknown = sorted(set(policies) - {"modules", "target_priority"})
    if unknown:
        raise ValueError(f"unsupported loadout policies: {unknown}")
    mode = str(policies.get("modules") or "enforce").strip().lower()
    if mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(f"unsupported modules policy {mode!r}")
    return mode


def _target_priority_policy(requirements: Mapping[str, Any]) -> str:
    policies = requirements.get("loadout_policies") or {}
    if not isinstance(policies, Mapping):
        raise ValueError("loadout_policies must be a mapping")
    mode = str(policies.get("target_priority") or "preserve").strip().lower()
    if mode not in {"enforce", "observe", "preserve"}:
        raise ValueError(f"unsupported target_priority policy {mode!r}")
    configured = requirements.get("target_priority")
    if mode == "preserve":
        if configured is not None:
            raise ValueError("preserved target_priority must not supply an order")
    else:
        validate_target_priority_order(configured)
    return mode


def _home_poison_swamp_stun_requirement(
    requirements: Mapping[str, Any],
) -> str | None:
    ultimate_weapons = requirements.get("ultimate_weapons")
    if ultimate_weapons is None:
        return None
    if not isinstance(ultimate_weapons, Mapping):
        raise ValueError("ultimate_weapons must be a mapping")
    for label, toggles in ultimate_weapons.items():
        if str(label or "").strip().lower() != "poison swamp":
            continue
        if not isinstance(toggles, Mapping):
            raise ValueError("Poison Swamp requirements must be a mapping")
        if "stun" not in toggles:
            return None
        state = str(toggles.get("stun") or "").strip().lower()
        if state not in {"on", "off"}:
            raise ValueError("Home Poison Swamp Stun must be on or off")
        return state
    return None


def _require_no_battle_home(frame, detector, detect_home_control_fn) -> None:
    detection = detector(frame)
    if detection.get("state") != "HOME_SCREEN":
        raise _SetupFailure(f"expected HOME_SCREEN, got {detection.get('state')!r}")
    home = detect_home_control_fn(frame)
    if home.control is not HomeBattleControl.NEW_BATTLE:
        raise _SetupFailure(f"expected NEW_BATTLE, got {home.control.value}")


def _wait_for(
    *,
    state: str,
    capture_fn,
    detector,
    sleep_fn,
    secondary: str | None = None,
    timeout: float = 8.0,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_fn()
        detection = detector(frame)
        if detection.get("state") == state and (
            secondary is None
            or secondary in set(detection.get("secondary_states") or ())
        ):
            return frame
        sleep_fn(0.25)
    suffix = f"/{secondary}" if secondary else ""
    raise _SetupFailure(f"timed out waiting for {state}{suffix}")


def _open_static(
    source,
    label,
    source_state,
    destination,
    capture_fn,
    detector,
    safe_tap_fn,
    sleep_fn,
    *,
    required_secondary: str | None = None,
):
    state = detector(source).get("state")
    if state != source_state:
        raise _SetupFailure(
            f"static navigation guard failed for {label}: "
            f"expected {source_state}, got {state!r}"
        )
    if not safe_tap_fn(label, dispatch="now"):
        raise _SetupFailure(f"static navigation failed for {label} from {state}")
    return _wait_for(
        state=destination,
        secondary=required_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _open_visible(
    source,
    label,
    source_state,
    destination,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    if detector(source).get("state") != source_state:
        raise _SetupFailure(f"visible navigation guard failed for {label}")
    if not tap_visible_fn(label, screenshot=source, retries=1):
        raise _SetupFailure(f"visible navigation failed for {label}")
    return _wait_for(
        state=destination,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _ensure_preset(
    frame,
    *,
    state,
    slot_secondary,
    slot_label,
    slot_region,
    capture_fn,
    detector,
    tap_visible_fn,
    measure_selection_fn,
    sleep_fn,
):
    detection = detector(frame)
    if detection.get("state") != state:
        raise _SetupFailure(f"preset parent state changed from {state}")
    if slot_secondary not in set(detection.get("secondary_states") or ()):
        raise _SetupFailure(f"preset identity missing: {slot_secondary}")
    selection = measure_selection_fn(frame, slot_region)
    if selection.selected:
        return frame, False
    if not tap_visible_fn(slot_label, screenshot=frame, retries=1):
        raise _SetupFailure(f"preset tap failed: {slot_label}")
    updated = _wait_for(
        state=state,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    if not measure_selection_fn(updated, slot_region).selected:
        raise _SetupFailure(f"preset did not become selected: {slot_label}")
    return updated, True


def _ensure_event_bots_top(
    frame,
    *,
    capture_fn,
    detector,
    swipe_fn,
    sleep_fn,
):
    current = frame
    for _ in range(4):
        detection = detector(current)
        if (
            detection.get("state") == "EVENT"
            and "EVENT_BOTS_SCREEN"
            in set(detection.get("secondary_states") or ())
        ):
            return current
        if detection.get("state") != "EVENT":
            raise _SetupFailure("Event Bots top-scroll guard lost EVENT")
        if not swipe_fn("gesture_targets.goto_top:event_bots"):
            raise _SetupFailure("Event Bots top swipe failed")
        sleep_fn(0.6)
        current = _wait_for(
            state="EVENT",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
    raise _SetupFailure("Event Bots preset evidence remained offscreen")


def _ensure_guardian_loadout(
    frame,
    required,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    required_chips = {str(chip).strip() for chip in required or ()}
    if required_chips == {"Fetch", "Summon", "Scout"}:
        replacements = (
            (
                "GUARDIAN_FETCH_EQUIPPED",
                "GUARDIAN_ATTACK_EQUIPPED",
                "indicators.guardian:attack_equipped",
                "buttons.guardian:fetch_inventory",
            ),
            (
                "GUARDIAN_SUMMON_EQUIPPED",
                "GUARDIAN_ALLY_EQUIPPED",
                "indicators.guardian:ally_equipped",
                "buttons.guardian:summon_inventory",
            ),
            (
                "GUARDIAN_SCOUT_EQUIPPED",
                None,
                None,
                "buttons.guardian:scout_inventory",
            ),
        )
    elif required_chips == {"Attack", "Ally", "Scout"}:
        replacements = (
            (
                "GUARDIAN_ATTACK_EQUIPPED",
                "GUARDIAN_FETCH_EQUIPPED",
                "indicators.guardian:fetch_equipped",
                "buttons.guardian:attack_inventory",
            ),
            (
                "GUARDIAN_ALLY_EQUIPPED",
                "GUARDIAN_SUMMON_EQUIPPED",
                "indicators.guardian:summon_equipped",
                "buttons.guardian:ally_inventory",
            ),
            (
                "GUARDIAN_SCOUT_EQUIPPED",
                None,
                None,
                "buttons.guardian:scout_inventory",
            ),
        )
    else:
        raise _SetupFailure(
            "unsupported Guardian loadout: " + ", ".join(sorted(required_chips))
        )

    desired = {
        f"GUARDIAN_{chip.upper()}_EQUIPPED" for chip in required_chips
    }
    current = frame
    changed: list[str] = []
    for (
        expected_secondary,
        replacement_source_secondary,
        wrong_label,
        inventory_label,
    ) in replacements:
        detected = set(detector(current).get("secondary_states") or ())
        if expected_secondary in detected:
            continue
        if (
            replacement_source_secondary is not None
            and replacement_source_secondary in detected
        ):
            current = _replace_guardian_chip(
                current,
                wrong_label=wrong_label,
                wrong_secondary=replacement_source_secondary,
                inventory_label=inventory_label,
                expected_secondary=expected_secondary,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            changed.append(
                expected_secondary.removeprefix("GUARDIAN_")
                .removesuffix("_EQUIPPED")
                .title()
            )
            continue

        # A prior fail-closed replacement can leave this slot empty after the
        # old chip was removed. Reacquire settled Guardian evidence and fill
        # the exact missing slot from a freshly matched inventory card.
        sleep_fn(GUARDIAN_INVENTORY_SETTLE_SECONDS)
        current = _wait_for(
            state="GUILD",
            secondary="GUILD_GUARDIAN_SCREEN",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        detected = set(detector(current).get("secondary_states") or ())
        if expected_secondary in detected:
            continue
        if (
            replacement_source_secondary is not None
            and replacement_source_secondary in detected
        ):
            current = _replace_guardian_chip(
                current,
                wrong_label=wrong_label,
                wrong_secondary=replacement_source_secondary,
                inventory_label=inventory_label,
                expected_secondary=expected_secondary,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            changed.append(
                expected_secondary.removeprefix("GUARDIAN_")
                .removesuffix("_EQUIPPED")
                .title()
            )
            continue
        if not tap_visible_fn(inventory_label, screenshot=current, retries=1):
            raise _SetupFailure(
                f"Guardian empty-slot target missing: {inventory_label}"
            )
        current = _wait_for(
            state="GUILD",
            secondary=expected_secondary,
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        changed.append(
            expected_secondary.removeprefix("GUARDIAN_")
            .removesuffix("_EQUIPPED")
            .title()
        )

    detected = set(detector(current).get("secondary_states") or ())
    missing = desired - detected
    if missing:
        raise _SetupFailure(
            "unsupported Guardian mismatch: " + ", ".join(sorted(missing))
        )
    return current, tuple(changed)


def _replace_guardian_chip(
    frame,
    *,
    wrong_label,
    wrong_secondary,
    inventory_label,
    expected_secondary,
    capture_fn,
    detector,
    tap_visible_fn,
    sleep_fn,
):
    if not tap_visible_fn(wrong_label, screenshot=frame, retries=1):
        raise _SetupFailure(f"known Guardian replacement source missing: {wrong_label}")
    emptied = _wait_for_guardian_inventory(
        removed_secondary=wrong_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    # The equipped slot can disappear one compositor frame before the chip
    # inventory accepts a replacement tap. Wait for that transition to settle
    # and reacquire current evidence instead of acting on the earliest empty
    # frame.
    sleep_fn(GUARDIAN_INVENTORY_SETTLE_SECONDS)
    emptied = _wait_for_guardian_inventory(
        removed_secondary=wrong_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    selected = tap_visible_fn(inventory_label, screenshot=emptied, retries=1)
    if not selected:
        raise _SetupFailure(f"Guardian inventory target missing: {inventory_label}")
    return _wait_for(
        state="GUILD",
        secondary=expected_secondary,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


def _wait_for_guardian_inventory(
    *,
    removed_secondary,
    capture_fn,
    detector,
    sleep_fn,
    timeout: float = 8.0,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_fn()
        detection = detector(frame)
        secondary = set(detection.get("secondary_states") or ())
        if (
            detection.get("state") == "GUILD"
            and "GUILD_GUARDIAN_SCREEN" in secondary
            and removed_secondary not in secondary
        ):
            return frame
        sleep_fn(0.25)
    raise _SetupFailure(
        f"Guardian source remained equipped: {removed_secondary}"
    )


def _return_home(
    frame,
    capture_fn,
    detector,
    detect_home_control_fn,
    safe_tap_fn,
    tap_visible_fn,
    sleep_fn,
):
    if detector(frame).get("state") == "HOME_SCREEN":
        _require_no_battle_home(frame, detector, detect_home_control_fn)
        return frame
    state = detector(frame).get("state")
    returned = False
    if state in {"EVENT", "GUILD"}:
        returned = tap_visible_fn(
            "buttons.return_to_game",
            screenshot=frame,
            retries=1,
        )
    if not returned:
        returned = safe_tap_fn(
            "navigation.goto_home",
            dispatch="now",
        )
    if not returned:
        raise _SetupFailure("Home navigation failed")
    home = _wait_for(
        state="HOME_SCREEN",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    _require_no_battle_home(home, detector, detect_home_control_fn)
    return home


def _recover_home(
    capture_fn,
    detector,
    detect_home_control_fn,
    safe_tap_fn,
    tap_visible_fn,
    sleep_fn,
) -> None:
    try:
        frame = capture_fn()
        if _is_not_enough_medals_dialog(frame):
            if not safe_tap_fn(
                NOT_ENOUGH_MEDALS_OK,
                dispatch="now",
                log_label="not_enough_medals_ok",
                verification=TapVerification(
                    screenshot=frame,
                    target_region=NOT_ENOUGH_MEDALS_REGION,
                    description="not_enough_medals:ok",
                    verifier=_is_not_enough_medals_dialog,
                ),
            ):
                return
            sleep_fn(0.8)
            frame = capture_fn()
        if detector(frame).get("state") == "HOME_SCREEN":
            return
        if detector(frame).get("state") == "PERKS":
            if not tap_visible_fn(
                "buttons.close:perks",
                screenshot=frame,
                retries=1,
            ):
                return
            home = _wait_for(
                state="HOME_SCREEN",
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            _require_no_battle_home(
                home,
                detector,
                detect_home_control_fn,
            )
            return
        _return_home(
            frame,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )
    except Exception:
        pass


def _is_not_enough_medals_dialog(
    frame,
    *,
    text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> bool:
    """Recognize the exact preset-switch rejection before dismissing it."""

    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        return False
    x, y, w, h = NOT_ENOUGH_MEDALS_REGION
    screen_h, screen_w = frame.shape[:2]
    if x < 0 or y < 0 or x + w > screen_w or y + h > screen_h:
        return False
    try:
        raw_text, confidence = text_fn(frame[y : y + h, x : x + w], psm=6)
    except Exception:
        return False
    normalized = " ".join(str(raw_text).upper().split())
    return (
        float(confidence) >= 70.0
        and "NOT ENOUGH MEDALS" in normalized
        and "MEDALS TO SWITCH PRESETS" in normalized
    )


__all__ = [
    "GcNoBattleSetupResult",
    "GcNoBattleSetupStatus",
    "run_gc_no_battle_setup",
]
