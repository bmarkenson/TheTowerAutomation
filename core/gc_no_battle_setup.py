"""Guarded GC preset correction at a verified no-battle Home boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Callable, Mapping

from core.battle_lifecycle import HomeBattleControl
from core.damage_adjuster import normalize_damage_percentage
from core.free_upgrade_locks import (
    inspect_free_upgrade_locks,
    normalize_free_upgrade_lock_requirements,
    select_workshop_upgrade_menu,
)
from core.home_battle import detect_home_battle_control
from core.gc_module_loadout import (
    evaluate_gc_module_loadout,
    ensure_gc_module_loadout,
    normalize_gc_module_requirements,
)
from core.gc_preflight import GC_SECTION_SPECS, validate_gc_preflight_screens
from core.input import TapVerification, safe_tap, swipe_now, tap_if_visible
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
from utils.logger import log
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


class GcNoBattleSetupStatus(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
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


class _SetupFailure(RuntimeError):
    pass


def run_gc_no_battle_setup(
    requirements: Mapping[str, Any],
    *,
    screenshot=None,
    waivers: Mapping[str, Any] | None = None,
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
    validate_configuration_fn: Callable[..., Any] = validate_gc_preflight_screens,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GcNoBattleSetupResult:
    """Correct supported persistent profile settings before a battle starts."""

    unsupported = _unsupported_requirement(requirements)
    if unsupported:
        return GcNoBattleSetupResult(
            GcNoBattleSetupStatus.UNSUPPORTED,
            unsupported,
        )

    module_mode = _module_policy(requirements)
    target_priority_mode = _target_priority_policy(requirements)
    active_waivers = dict(waivers or {})
    evidence: dict[str, Any] = {
        "loadout_policies": {
            "modules": module_mode,
            "target_priority": target_priority_mode,
        },
        "waivers": active_waivers,
    }
    current = screenshot if screenshot is not None else capture_fn()
    section_specs = _configuration_section_specs(requirements)
    current_check = "home_boundary"
    try:
        _require_no_battle_home(current, detector, detect_home_control_fn)

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
        else:
            preset = _preset_spec(current_check, requirements)
            cards = _ensure_preset(
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
            evidence[current_check] = requirements[current_check]
        current = _return_home(
            cards,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )

        current_check = "workshop_preset"
        workshop = _open_static(
            current,
            "navigation.goto_workshop_home",
            "HOME_SCREEN",
            "WORKSHOP",
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
        else:
            preset = _preset_spec(current_check, requirements)
            workshop = _ensure_preset(
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
            evidence[current_check] = requirements[current_check]
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
            workshop = lock_result.screenshot

        current_check = "ultimate_weapons"
        home_stun_required = _home_poison_swamp_stun_requirement(requirements)
        if home_stun_required is not None and current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
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
            log(
                "[GC_NO_BATTLE] Poison Swamp Stun verified "
                f"{home_stun_required} at Home"
                + (" after correction" if stun_result.changed else ""),
                "INFO",
            )
        elif isinstance(requirements.get(current_check), Mapping):
            evidence[current_check] = {
                "boundary": HomeBattleControl.NEW_BATTLE.value,
                "checked": [],
                "observations": {},
                "valid": True,
                "reason": "no_supported_home_controls_required",
            }
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
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
        else:
            preset = _preset_spec(current_check, requirements)
            bots = _ensure_preset(
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
            evidence[current_check] = requirements[current_check]
        current = _return_home(
            bots,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )

        current_check = "guardian_chips"
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
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
        else:
            guardians = _ensure_guardian_loadout(
                guardians,
                requirements[current_check],
                capture_fn,
                detector,
                tap_visible_fn,
                sleep_fn,
            )
            evidence[current_check] = list(requirements[current_check])
        current = _return_home(
            guardians,
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )

        current_check = "modules"
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                requirements.get(current_check),
                active_waivers[current_check],
            )
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
            module_evidence = ensure_modules_fn(
                requirements["modules"],
                screenshot=modules,
                capture_fn=capture_fn,
                detector=detector,
                safe_tap_fn=safe_tap_fn,
                swipe_fn=swipe_fn,
                sleep_fn=sleep_fn,
            )
            if not module_evidence.valid:
                raise _SetupFailure(
                    "module loadout remained invalid after correction"
                )
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

        current_check = "target_priority"
        target_priority_requirement = requirements.get("target_priority")
        if current_check in active_waivers:
            evidence[current_check] = _waived_evidence(
                current_check,
                target_priority_requirement,
                active_waivers[current_check],
            )
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

        configuration = validate_configuration_fn(
            cards_screen=cards,
            workshop_screen=workshop,
            bots_screen=bots,
            guardians_screen=guardians,
            detector=detector,
            section_specs=section_specs,
        )
        evidence["configuration"] = configuration.as_dict()
    except Exception as exc:
        _recover_home(
            capture_fn,
            detector,
            detect_home_control_fn,
            safe_tap_fn,
            tap_visible_fn,
            sleep_fn,
        )
        log(f"[GC_NO_BATTLE] Setup failed: {exc}", "ERROR")
        return GcNoBattleSetupResult(
            GcNoBattleSetupStatus.FAILED,
            str(exc),
            evidence,
            current_check,
        )

    log(
        "[GC_NO_BATTLE] Profile Home settings verified/corrected before Battle",
        "INFO",
    )
    return GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "supported no-battle requirements verified",
        evidence,
    )


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
        return frame
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
    return updated


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

    detected = set(detector(current).get("secondary_states") or ())
    missing = desired - detected
    if missing:
        raise _SetupFailure(
            "unsupported Guardian mismatch: " + ", ".join(sorted(missing))
        )
    return current


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
