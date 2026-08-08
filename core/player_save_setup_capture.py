"""Project one forced player-save acquisition into existing authoring owners.

This module deliberately defines no loadout or Strategy schema.  It selects
only observations authorized by the resolved mapping's explicit validation or
compatibility allowlist and runs every selected value through
:mod:`core.strategy_authoring`'s existing setting normalizer.  Values that
cannot be represented by that owner remain explicit unresolved evidence.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.player_save import save_observation_supports_requirement
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
)
from core.strategy_authoring import (
    AUTHORING_SCHEMA_VERSION,
    FARM_SETTING_REGISTRY,
    normalize_strategy_source,
)


SETUP_CAPTURE_SCHEMA_VERSION = 1

_DIRECT_CHECKS = (
    "cards_deck",
    "card_recharge_modes",
    "workshop_preset",
    "free_upgrade_locks",
    "bots_preset",
    "guardian_chips",
    "auto_pick_perks",
    "perk_bans",
    "perk_auto_pick_order",
)
_LOCAL_LOADOUT_CHECKS = ("modules", "target_priority")
_ULTIMATE_COMPONENT_CHECKS = (
    "ultimate_weapon_primaries",
    "poison_swamp_stun",
    "spotlight_missiles",
)
_UNRESOLVED_AUTHORING_SETTINGS = ("damage_slider", "orb_distance")


class SetupCaptureError(ValueError):
    """A forced-save bundle cannot safely become authoring evidence."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "setup_capture_invalid",
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def project_forced_save_setup(
    acquisition: PlayerSaveAcquisitionBundle,
) -> dict[str, Any]:
    """Return a safe authoring projection from one exact forced serialization.

    The result contains no raw save bytes, source path, device target, or
    unclassified save field.  It is review evidence only and grants no runtime
    selection, publication, application, or input authority.
    """

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise SetupCaptureError(
            "Setup capture requires a typed player-save acquisition",
            code="setup_capture_requires_typed_acquisition",
        )
    if acquisition.acquisition_type is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION:
        raise SetupCaptureError(
            "Setup capture requires a newly forced player-save serialization",
            code="setup_capture_requires_forced_serialization",
        )
    if (
        acquisition.status is not PlayerSaveAcquisitionStatus.COMPLETE
        or acquisition.complete is not True
        or acquisition.snapshot is None
        or acquisition.binding is None
        or acquisition.captured_at is None
    ):
        raise SetupCaptureError(
            "Setup capture requires a complete, stable, exact-target save",
            code="setup_capture_acquisition_incomplete",
        )

    snapshot = acquisition.snapshot
    mapping_id = str(getattr(snapshot, "mapping_id", None) or "").strip()
    mapping_maturity = str(
        getattr(snapshot, "mapping_maturity", None) or ""
    ).strip()
    checks = getattr(snapshot, "checks", None)
    if (
        not mapping_id
        or getattr(snapshot, "shape_valid", False) is not True
        or not isinstance(checks, Mapping)
    ):
        raise SetupCaptureError(
            "Setup capture requires a supported, shape-valid save mapping",
            code="setup_capture_mapping_unavailable",
        )
    validated = {
        str(check_id)
        for check_id in getattr(snapshot, "validated_checks", ()) or ()
    }

    settings: dict[str, Any] = {}
    captured_checks: set[str] = set()
    unresolved: list[dict[str, Any]] = []

    def trusted_evidence(
        check_id: str,
    ) -> tuple[Optional[Any], Optional[str], str]:
        evidence = checks.get(check_id)
        if mapping_maturity != "validated" and check_id not in validated:
            return (
                None,
                "save check is outside the resolved mapping validation allowlist",
                "unresolved",
            )
        if evidence is None:
            return None, "save mapping does not expose this setup field", "unresolved"
        if getattr(evidence, "status", None) != "observed":
            return (
                None,
                str(
                    getattr(evidence, "reason", None)
                    or "save mapping could not observe this setup field"
                ),
                "unresolved",
            )
        if getattr(evidence, "complete", None) is not True:
            return (
                deepcopy(getattr(evidence, "value", None)),
                str(
                    getattr(evidence, "reason", None)
                    or "save observation is incomplete"
                ),
                "unresolved",
            )
        value = deepcopy(getattr(evidence, "value", None))
        if not save_observation_supports_requirement(check_id, value, evidence):
            return (
                value,
                "observed value is outside the resolved save mapping's validated requirement authority",
                "unsupported_authoring_value",
            )
        if check_id == "free_upgrade_locks":
            diagnostics = getattr(evidence, "diagnostics", None)
            diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
            unmanaged = diagnostics.get("unmanaged_locks")
            unmapped_count = diagnostics.get("unmapped_locked_slot_count")
            if unmanaged != [] or unmapped_count != 0:
                return (
                    value,
                    "save reports locked slots outside the managed Farm lock mapping",
                    "unresolved",
                )
            if not isinstance(value, (list, tuple)) or set(value) != set(
                FARM_FREE_UPGRADE_LOCKS
            ):
                return (
                    value,
                    "save does not prove the exact managed Farm lock set",
                    "unsupported_authoring_value",
                )
            value = list(FARM_FREE_UPGRADE_LOCKS)
        return value, None, "captured"

    def unresolved_entry(
        setting_id: str,
        check_ids: tuple[str, ...],
        reason: str,
        *,
        observed_value: Any = None,
        observed: bool = False,
        status: str = "unresolved",
    ) -> None:
        entry: dict[str, Any] = {
            "setting_id": setting_id,
            "display_name": (
                FARM_SETTING_REGISTRY[setting_id].display_name
                if setting_id in FARM_SETTING_REGISTRY
                else "First Perk Choice"
            ),
            "source_check_ids": sorted(check_ids),
            "status": status,
            "reason": str(reason or "setup field is unresolved"),
        }
        if observed:
            entry["observed_value"] = deepcopy(observed_value)
        unresolved.append(entry)

    def add_setting(
        setting_id: str,
        check_ids: tuple[str, ...],
        candidate: Any,
    ) -> None:
        try:
            normalized = FARM_SETTING_REGISTRY[setting_id].normalizer(candidate)
        except (TypeError, ValueError) as exc:
            unresolved_entry(
                setting_id,
                check_ids,
                f"existing Strategy authoring cannot represent the observed value: {exc}",
                observed_value=candidate,
                observed=True,
                status="unsupported_authoring_value",
            )
            return
        settings[setting_id] = normalized
        captured_checks.update(check_ids)

    for check_id in _DIRECT_CHECKS:
        value, reason, status = trusted_evidence(check_id)
        if reason is not None:
            unresolved_entry(
                check_id,
                (check_id,),
                reason,
                observed_value=value,
                observed=value is not None,
                status=status,
            )
            continue
        add_setting(check_id, (check_id,), value)

    for check_id in _LOCAL_LOADOUT_CHECKS:
        value, reason, status = trusted_evidence(check_id)
        if reason is not None:
            unresolved_entry(
                check_id,
                (check_id,),
                reason,
                observed_value=value,
                observed=value is not None,
                status=status,
            )
            continue
        add_setting(check_id, (check_id,), {"local": value})

    ultimate_values: dict[str, Any] = {}
    ultimate_failures: list[str] = []
    ultimate_status = "unresolved"
    for check_id in _ULTIMATE_COMPONENT_CHECKS:
        value, reason, status = trusted_evidence(check_id)
        if value is not None:
            ultimate_values[check_id] = value
        if reason is not None:
            ultimate_failures.append(f"{check_id}: {reason}")
            if status == "unsupported_authoring_value":
                ultimate_status = status
    if ultimate_failures:
        unresolved_entry(
            "ultimate_weapons",
            _ULTIMATE_COMPONENT_CHECKS,
            "all validated Ultimate Weapon components are required; "
            + "; ".join(ultimate_failures),
            observed_value=ultimate_values,
            observed=bool(ultimate_values),
            status=ultimate_status,
        )
    else:
        primaries = ultimate_values["ultimate_weapon_primaries"]
        if not isinstance(primaries, Mapping):
            unresolved_entry(
                "ultimate_weapons",
                _ULTIMATE_COMPONENT_CHECKS,
                "Ultimate Weapon primary observations are not an object",
                observed_value=primaries,
                observed=True,
            )
        else:
            combined = deepcopy(dict(primaries))
            poison = combined.get("Poison Swamp")
            spotlight = combined.get("Spotlight")
            if not isinstance(poison, Mapping) or not isinstance(spotlight, Mapping):
                unresolved_entry(
                    "ultimate_weapons",
                    _ULTIMATE_COMPONENT_CHECKS,
                    "validated components do not identify Poison Swamp and Spotlight",
                    observed_value=combined,
                    observed=True,
                )
            else:
                combined["Poison Swamp"] = {
                    **dict(poison),
                    "stun": ultimate_values["poison_swamp_stun"],
                }
                combined["Spotlight"] = {
                    **dict(spotlight),
                    "missiles": ultimate_values["spotlight_missiles"],
                }
                add_setting(
                    "ultimate_weapons",
                    _ULTIMATE_COMPONENT_CHECKS,
                    combined,
                )

    for setting_id in _UNRESOLVED_AUTHORING_SETTINGS:
        value, reason, status = trusted_evidence(setting_id)
        unresolved_entry(
            setting_id,
            (setting_id,),
            reason or "save observation is not yet mapped into capture authoring",
            observed_value=value,
            observed=reason is None,
            status=status if reason is not None else "unresolved",
        )

    first_perk, first_perk_reason, first_perk_status = trusted_evidence(
        "perk_first_choice"
    )
    if first_perk_reason is None:
        unresolved_entry(
            "perk_first_choice",
            ("perk_first_choice",),
            "the current Strategy authoring registry does not own First Perk Choice",
            observed_value=first_perk,
            observed=True,
            status="observed_not_authorable",
        )
    elif "perk_first_choice" in checks or "perk_first_choice" in validated:
        unresolved_entry(
            "perk_first_choice",
            ("perk_first_choice",),
            first_perk_reason,
            observed_value=first_perk,
            observed=first_perk is not None,
            status=first_perk_status,
        )

    return {
        "schema_version": SETUP_CAPTURE_SCHEMA_VERSION,
        "status": "complete" if not unresolved else "partial",
        "mapping_id": mapping_id,
        "mapping_maturity": mapping_maturity,
        "captured_at": acquisition.captured_at.isoformat(),
        "acquisition": acquisition.redacted_provenance(),
        "settings": deepcopy(settings),
        "captured_check_ids": sorted(captured_checks),
        "unresolved": sorted(
            unresolved,
            key=lambda item: (str(item["setting_id"]), item["reason"]),
        ),
        "publication_activates_strategy": False,
        "saving_activates_strategy": False,
    }


def strategy_source_from_capture(
    capture: Mapping[str, Any],
    *,
    strategy_id: object,
    display_name: object,
    tier: object,
    base: object = None,
) -> dict[str, Any]:
    """Build a normal schema-3 Strategy source from a reviewed projection."""

    if not isinstance(capture, Mapping) or capture.get("schema_version") != 1:
        raise SetupCaptureError(
            "Capture preview is invalid",
            code="invalid_setup_capture_preview",
            field="capture",
        )
    raw_settings = capture.get("settings")
    if not isinstance(raw_settings, Mapping) or not raw_settings:
        raise SetupCaptureError(
            "Capture preview has no representable Strategy settings",
            code="empty_setup_capture",
            field="capture",
        )
    settings = {
        str(setting_id): {
            "policy": "enforce",
            "value": deepcopy(value),
        }
        for setting_id, value in raw_settings.items()
    }
    source: dict[str, Any] = {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "kind": "strategy",
        "id": strategy_id,
        "display_name": display_name,
        "family": "farm",
        "tier": tier,
        "version": 1,
        "settings": settings,
    }
    if base is not None:
        source["base"] = deepcopy(base)
    try:
        return normalize_strategy_source(source)
    except (TypeError, ValueError) as exc:
        raise SetupCaptureError(
            f"Captured Strategy draft is invalid: {exc}",
            code="invalid_captured_strategy_source",
            field="source",
        ) from exc


def module_preset_source_from_capture(
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the existing local Module selector from a capture preview."""

    if not isinstance(capture, Mapping) or capture.get("schema_version") != 1:
        raise SetupCaptureError(
            "Capture preview is invalid",
            code="invalid_setup_capture_preview",
            field="capture",
        )
    settings = capture.get("settings")
    modules = settings.get("modules") if isinstance(settings, Mapping) else None
    try:
        normalized = FARM_SETTING_REGISTRY["modules"].normalizer(modules)
    except (TypeError, ValueError) as exc:
        raise SetupCaptureError(
            f"Capture does not contain a complete Module loadout: {exc}",
            code="captured_modules_unavailable",
            field="capture",
        ) from exc
    return normalized


__all__ = [
    "SETUP_CAPTURE_SCHEMA_VERSION",
    "SetupCaptureError",
    "module_preset_source_from_capture",
    "project_forced_save_setup",
    "strategy_source_from_capture",
]
