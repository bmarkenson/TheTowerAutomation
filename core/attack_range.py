"""Versioned effective Attack Range calculation from player-save state.

The game stores the selected Range lab level separately from the displayed
Attack Range.  This module owns the reusable semantic calculation so callers
do not need to reconstruct it from Orb Distance presets or UI labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import struct
from typing import Any, Mapping, Sequence


ATTACK_RANGE_CAPABILITY_ID = "thetower.player_save.effective_attack_range.v1"


class AttackRangeError(ValueError):
    """The effective-range contract or one of its inputs is invalid."""


@dataclass(frozen=True)
class AttackRangeInputs:
    """Normalized inputs to the effective Attack Range formula.

    ``module_bonus_game_units`` uses the game's internal one-tenth-meter unit
    because the native pipeline adds it before the final meter conversion.
    """

    workshop_level: int
    current_level: int
    selected_lab_level: int
    range_card_active: bool
    range_card_level: int
    round_active: bool = True
    module_bonus_game_units: Decimal = Decimal("0")


@dataclass(frozen=True)
class AttackRangeCalculation:
    """Binary32 intermediates and the formatted value from one calculation."""

    workshop_level_base_meters: Decimal
    in_battle_level_delta_meters: Decimal
    lab_multiplier: Decimal
    card_multiplier: Decimal
    module_bonus_meters: Decimal
    pre_compression_meters: Decimal
    effective_meters: Decimal
    display_meters: Decimal
    display_value: str


@dataclass(frozen=True)
class AttackRangeEvidence:
    """A privacy-safe effective-range observation from one decoded save."""

    status: str
    value: str | None
    source_fields: tuple[str, ...]
    complete: bool
    stable: bool
    scope: str | None
    reason: str
    authority: Mapping[str, Any]
    diagnostics: Mapping[str, Any]


def attack_range_contract_fingerprints(
    contract: Mapping[str, Any],
) -> tuple[str, str]:
    """Return semantic and raw-binding fingerprints for a valid contract."""

    normalized = _validated_contract(contract)
    semantic = {
        key: normalized[key]
        for key in (
            "schema_version",
            "capability_id",
            "evidence_level",
            "supported_game_versions",
            "forward_policy",
            "temporal_scopes",
            "level_bounds",
            "formula",
        )
    }
    binding = {
        "schema_version": normalized["schema_version"],
        "source_fields": normalized["source_fields"],
        "array_lengths": normalized["array_lengths"],
        "indices": normalized["indices"],
    }
    return _fingerprint(semantic), _fingerprint(binding)


def calculate_effective_attack_range(
    inputs: AttackRangeInputs,
    contract: Mapping[str, Any],
) -> AttackRangeCalculation:
    """Calculate effective Attack Range using one versioned game contract."""

    spec = _validated_contract(contract)
    bounds = spec["level_bounds"]
    _require_level("workshop_level", inputs.workshop_level, bounds["workshop"])
    _require_level("current_level", inputs.current_level, bounds["current"])
    _require_level(
        "selected_lab_level",
        inputs.selected_lab_level,
        bounds["selected_lab"],
    )
    _require_level("range_card_level", inputs.range_card_level, bounds["card"])
    if type(inputs.range_card_active) is not bool:
        raise AttackRangeError("range_card_active_not_exact_boolean")
    if type(inputs.round_active) is not bool:
        raise AttackRangeError("round_active_not_exact_boolean")
    if inputs.round_active and inputs.current_level < inputs.workshop_level:
        raise AttackRangeError("current_range_level_below_workshop_level")

    formula = spec["formula"]
    base = _binary32(formula["base_game_units"], "base_game_units")
    workshop_step = _binary32(
        formula["workshop_step_game_units"],
        "workshop_step_game_units",
    )
    lab_step = _binary32(
        formula["lab_step_multiplier"],
        "lab_step_multiplier",
    )
    module_bonus = _binary32(
        inputs.module_bonus_game_units,
        "module_bonus_game_units",
    )
    if module_bonus < 0:
        raise AttackRangeError("module_bonus_game_units_negative")

    workshop_game_units = _f32_add(
        base,
        _f32_mul(workshop_step, inputs.workshop_level),
    )
    current_game_units = _f32_add(
        base,
        _f32_mul(workshop_step, inputs.current_level),
    )
    selected_game_units = (
        current_game_units if inputs.round_active else workshop_game_units
    )
    lab_multiplier = _f32_add(
        1,
        _f32_mul(lab_step, inputs.selected_lab_level),
    )
    card_multipliers = formula["card_multipliers"]
    if inputs.range_card_active and inputs.range_card_level == 0:
        raise AttackRangeError("active_range_card_has_no_unlocked_level")
    card_multiplier = (
        _binary32(
            card_multipliers[inputs.range_card_level],
            "card_multiplier",
        )
        if inputs.range_card_active
        else _binary32(1, "card_multiplier")
    )
    pre_compression = _f32_add(
        _f32_mul(
            _f32_mul(lab_multiplier, selected_game_units),
            card_multiplier,
        ),
        module_bonus,
    )

    compression = formula["compression"]
    threshold = _binary32(
        compression["threshold_game_units"],
        "threshold_game_units",
    )
    cap = _binary32(compression["cap_game_units"], "cap_game_units")
    span_divisor = _binary32(
        compression["span_divisor_game_units"],
        "span_divisor_game_units",
    )
    reduction = _binary32(
        compression["reduction_constant_binary32"],
        "reduction_constant_binary32",
    )
    bounded = min(pre_compression, cap)
    compression_term = _f32_mul(
        _f32_div(
            _f32_sub(bounded, threshold),
            span_divisor,
        ),
        reduction,
    )
    factor = _f32_add(compression_term, 1)
    if pre_compression < threshold:
        factor = _binary32(1, "compression_identity_factor")
    effective_game_units = _f32_mul(pre_compression, factor)

    display = formula["display"]
    scale = _binary32(display["scale_to_meters"], "scale_to_meters")
    workshop_meters = _f32_mul(workshop_game_units, scale)
    current_meters = _f32_mul(current_game_units, scale)
    in_battle_upgrade_meters = _f32_sub(
        current_meters,
        workshop_meters,
    )
    module_bonus_meters = _f32_mul(module_bonus, scale)
    pre_compression_meters = _f32_mul(pre_compression, scale)
    effective_meters = _f32_mul(effective_game_units, scale)
    quantum = Decimal(1).scaleb(-display["decimal_places"])
    display_meters = Decimal.from_float(effective_meters).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    display_text = f"{display_meters:.{display['decimal_places']}f}"
    unit = display["unit"]
    return AttackRangeCalculation(
        workshop_level_base_meters=_float_decimal(workshop_meters),
        in_battle_level_delta_meters=_float_decimal(
            in_battle_upgrade_meters
        ),
        lab_multiplier=_float_decimal(lab_multiplier),
        card_multiplier=_float_decimal(card_multiplier),
        module_bonus_meters=_float_decimal(module_bonus_meters),
        pre_compression_meters=_float_decimal(pre_compression_meters),
        effective_meters=_float_decimal(effective_meters),
        display_meters=display_meters,
        display_value=f"{display_text}{unit}",
    )


def effective_attack_range_from_save(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> AttackRangeEvidence:
    """Extract and calculate effective Attack Range, fail closed."""

    raw_contract = mapping.get("effective_attack_range")
    source_fields = _declared_source_fields(raw_contract)
    try:
        contract = _validated_contract(raw_contract)
        semantic_fingerprint, binding_fingerprint = (
            attack_range_contract_fingerprints(contract)
        )
    except AttackRangeError as exc:
        return _unavailable(
            source_fields,
            f"effective_attack_range_contract_invalid:{exc}",
        )

    fields = contract["source_fields"]
    game_version = _exact_int(decoded.get(fields["game_version"]))
    if game_version not in contract["supported_game_versions"]:
        return _unavailable(
            source_fields,
            "effective_attack_range_game_version_unsupported",
            contract=contract,
            semantic_fingerprint=semantic_fingerprint,
            binding_fingerprint=binding_fingerprint,
        )
    round_active = decoded.get(fields["round_active"])
    if type(round_active) is not bool:
        return _unavailable(
            source_fields,
            "effective_attack_range_round_state_invalid",
            contract=contract,
            semantic_fingerprint=semantic_fingerprint,
            binding_fingerprint=binding_fingerprint,
        )
    indices = contract["indices"]
    lengths = contract["array_lengths"]
    bounds = contract["level_bounds"]
    try:
        workshop_levels = _exact_sequence(
            decoded.get(fields["workshop_levels"]),
            lengths["workshop_levels"],
            "workshop_levels",
        )
        current_levels = _exact_sequence(
            decoded.get(fields["current_levels"]),
            lengths["current_levels"],
            "current_levels",
        )
        card_active = _exact_sequence(
            decoded.get(fields["card_active"]),
            lengths["card_active"],
            "card_active",
        )
        card_levels = _exact_sequence(
            decoded.get(fields["card_levels"]),
            lengths["card_levels"],
            "card_levels",
        )
        research_levels = _exact_sequence(
            decoded.get(fields["research_levels"]),
            lengths["research_levels"],
            "research_levels",
        )
        workshop_level = _indexed_int(
            workshop_levels,
            indices["workshop_range"],
            "workshop_level",
        )
        current_level = _indexed_int(
            current_levels,
            indices["current_range"],
            "current_level",
        )
        selected_lab_level = _required_int(
            decoded.get(fields["selected_lab_level"]),
            "selected_lab_level",
        )
        researched_lab_level = _indexed_int(
            research_levels,
            indices["range_lab_research"],
            "range_lab_research_level",
        )
        range_card_active = card_active[indices["range_card"]]
        if type(range_card_active) is not bool:
            raise AttackRangeError("range_card_active_not_exact_boolean")
        range_card_level = _indexed_int(
            card_levels,
            indices["range_card"],
            "range_card_level",
        )
        _require_level("workshop_level", workshop_level, bounds["workshop"])
        _require_level("current_level", current_level, bounds["current"])
        _require_level(
            "selected_lab_level",
            selected_lab_level,
            bounds["selected_lab"],
        )
        _require_level(
            "range_lab_research_level",
            researched_lab_level,
            bounds["range_lab_research"],
        )
        if selected_lab_level > researched_lab_level:
            raise AttackRangeError("selected_range_lab_exceeds_researched_level")
        _require_level("range_card_level", range_card_level, bounds["card"])
        if round_active and current_level < workshop_level:
            raise AttackRangeError("current_range_level_below_workshop_level")
        module_bonus = _module_range_bonus(decoded, contract)
        calculation = calculate_effective_attack_range(
            AttackRangeInputs(
                workshop_level=workshop_level,
                current_level=current_level,
                selected_lab_level=selected_lab_level,
                range_card_active=range_card_active,
                range_card_level=range_card_level,
                round_active=round_active,
                module_bonus_game_units=module_bonus,
            ),
            contract,
        )
    except AttackRangeError as exc:
        return _unavailable(
            source_fields,
            f"effective_attack_range_input_invalid:{exc}",
            contract=contract,
            semantic_fingerprint=semantic_fingerprint,
            binding_fingerprint=binding_fingerprint,
        )

    stable = not round_active or current_level == bounds["current"][1]
    scope = contract["temporal_scopes"][
        "active" if round_active else "inactive"
    ]
    authority = _authority(
        contract,
        semantic_fingerprint=semantic_fingerprint,
        binding_fingerprint=binding_fingerprint,
        stable=stable,
        scope=scope,
    )
    diagnostics = {
        "workshop_level": workshop_level,
        "level_source": "upgradeLevel" if round_active else "upgradeWorkshopLevel",
        "in_battle_range_upgrades": (
            current_level - workshop_level if round_active else None
        ),
        "current_total_level": current_level if round_active else None,
        "selected_lab_level": selected_lab_level,
        "researched_lab_level": researched_lab_level,
        "range_card_active": range_card_active,
        "range_card_level": range_card_level,
        "module_bonus_meters": str(calculation.module_bonus_meters),
        "pre_compression_meters": str(calculation.pre_compression_meters),
        "effective_meters": str(calculation.effective_meters),
        "stable": stable,
        "scope": scope,
    }
    return AttackRangeEvidence(
        status="observed",
        value=calculation.display_value,
        source_fields=source_fields,
        complete=stable,
        stable=stable,
        scope=scope,
        reason=(
            ""
            if stable
            else "effective_attack_range_can_still_upgrade"
        ),
        authority=authority,
        diagnostics=diagnostics,
    )


def _module_range_bonus(
    decoded: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> Decimal:
    fields = contract["source_fields"]
    indices = contract["indices"]
    formula = contract["formula"]
    bounds = contract["level_bounds"]
    lengths = contract["array_lengths"]

    primary = _exact_sequence(
        decoded.get(fields["primary_modules"]),
        lengths["primary_modules"],
        "primary_modules",
    )
    primary_item = primary[indices["cannon_primary"]]
    primary_base = _module_item_range_bonus(
        primary_item,
        formula,
        bounds,
        "cannon_primary",
        effects_length=lengths["module_effects"],
    )
    primary_bonus = _binary32(
        float(primary_base),
        "primary_module_range_bonus",
    )

    slots = _exact_sequence(
        decoded.get(fields["assist_module_slots"]),
        lengths["assist_module_slots"],
        "assist_module_slots",
    )
    by_type: dict[int, Mapping[str, Any]] = {}
    for slot in slots:
        if not isinstance(slot, Mapping) or slot.get("__class__") != "AssistModuleSlot":
            raise AttackRangeError("assist_module_slot_type_changed")
        slot_type = _required_int(slot.get("type"), "assist_module_slot_type")
        if (
            slot_type not in range(lengths["assist_module_slots"])
            or slot_type in by_type
        ):
            raise AttackRangeError("assist_module_slot_membership_changed")
        if type(slot.get("unlocked")) is not bool:
            raise AttackRangeError("assist_module_slot_unlocked_not_boolean")
        by_type[slot_type] = slot
    if set(by_type) != set(range(lengths["assist_module_slots"])):
        raise AttackRangeError("assist_module_slot_membership_changed")

    cannon_slot = by_type[indices["cannon_assist_type"]]
    if cannon_slot["unlocked"] is not True:
        if cannon_slot.get("equippedModule") is not None:
            raise AttackRangeError("locked_cannon_assist_has_equipped_module")
        return _float_decimal(primary_bonus)
    assist_item = cannon_slot.get("equippedModule")
    assist_base = _module_item_range_bonus(
        assist_item,
        formula,
        bounds,
        "cannon_assist",
        effects_length=lengths["module_effects"],
    )
    if assist_base == 0:
        return _float_decimal(primary_bonus)
    substat_level = _required_int(
        cannon_slot.get("substatEfficiencyLevel"),
        "assist_substat_efficiency_level",
    )
    _require_level(
        "assist_substat_efficiency_level",
        substat_level,
        bounds["assist_substat"],
    )
    research = _exact_sequence(
        decoded.get(fields["research_levels"]),
        lengths["research_levels"],
        "research_levels",
    )
    research_level = _indexed_int(
        research,
        indices["assist_substat_lab"],
        "assist_substat_lab_level",
    )
    _require_level(
        "assist_substat_lab_level",
        research_level,
        bounds["assist_substat_lab"],
    )
    step_double = float(
        _decimal(
            formula["assist_substat_efficiency_step"],
            "assist_substat_efficiency_step",
        )
    )
    stone_efficiency = _binary32(
        substat_level * step_double + step_double,
        "assist_stone_efficiency",
    )
    research_efficiency = _f32_mul(
        _binary32(research_level, "assist_research_level"),
        _binary32(step_double, "assist_research_step"),
    )
    efficiency = _f32_add(research_efficiency, stone_efficiency)
    assist_contribution = _binary32(
        float(assist_base) * float(efficiency),
        "assist_module_range_contribution",
    )
    return _float_decimal(
        _f32_add(
            primary_bonus,
            assist_contribution,
        )
    )


def _module_item_range_bonus(
    item: Any,
    formula: Mapping[str, Any],
    bounds: Mapping[str, Sequence[int]],
    label: str,
    effects_length: int = 8,
) -> Decimal:
    if item is None:
        return Decimal(0)
    if not isinstance(item, Mapping) or item.get("__class__") != "ModuleItem":
        raise AttackRangeError(f"{label}_module_item_type_changed")
    info_index = _required_int(item.get("infoIndex"), f"{label}_info_index")
    if info_index < 0:
        raise AttackRangeError(f"{label}_info_index_negative")
    rarity = _required_int(item.get("currentRarity"), f"{label}_rarity")
    level = _required_int(item.get("level"), f"{label}_level")
    _require_level(f"{label}_rarity", rarity, bounds["module_rarity"])
    _require_level(f"{label}_level", level, bounds["module_level"])
    effects = _exact_sequence(
        item.get("effects"),
        effects_length,
        f"{label}_effects",
    )
    effect_bounds = bounds["module_effect"]
    if any(
        _exact_int(value) is None
        or not effect_bounds[0] <= int(value) <= effect_bounds[1]
        for value in effects
    ):
        raise AttackRangeError(f"{label}_effects_outside_domain")
    required_levels = formula["module_effect_slot_required_levels"]
    range_effects = formula["range_module_effect_game_units"]
    common_rarity = formula["common_module_rarity"]
    matches = [
        (slot_index, int(raw_effect))
        for slot_index, raw_effect in enumerate(effects)
        if str(raw_effect) in range_effects
    ]
    if len(matches) > 1:
        raise AttackRangeError(f"{label}_duplicate_range_effect_cluster")
    if not matches:
        return Decimal(0)
    slot_index, effect_id = matches[0]
    if level < required_levels[slot_index]:
        return Decimal(0)
    if rarity == common_rarity and slot_index > 0:
        return Decimal(0)
    return _decimal(
        range_effects[str(effect_id)],
        f"range_module_effect_{effect_id}",
    )


def _validated_contract(raw: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "capability_id",
        "audit_id",
        "evidence_level",
        "supported_game_versions",
        "forward_policy",
        "temporal_scopes",
        "source_fields",
        "array_lengths",
        "indices",
        "level_bounds",
        "formula",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise AttackRangeError("top_level_shape")
    contract = dict(raw)
    if (
        contract["schema_version"] != 1
        or contract["capability_id"] != ATTACK_RANGE_CAPABILITY_ID
        or not str(contract["audit_id"] or "").strip()
        or contract["evidence_level"] != "installed_binary_cross_channel"
        or contract["forward_policy"] != "exact_game_versions"
        or contract["temporal_scopes"]
        != {
            "active": "current_active_round",
            "inactive": "configured_out_of_round",
        }
    ):
        raise AttackRangeError("identity_or_scope")
    versions = contract["supported_game_versions"]
    if (
        not _is_sequence(versions)
        or not versions
        or any(_exact_int(value) is None or int(value) <= 0 for value in versions)
        or len(versions) != len(set(versions))
    ):
        raise AttackRangeError("supported_game_versions")

    expected_fields = {
        "game_version",
        "round_active",
        "workshop_levels",
        "current_levels",
        "selected_lab_level",
        "card_active",
        "card_levels",
        "primary_modules",
        "assist_module_slots",
        "research_levels",
    }
    fields = contract["source_fields"]
    if (
        not isinstance(fields, Mapping)
        or set(fields) != expected_fields
        or any(not isinstance(value, str) or not value for value in fields.values())
        or len(set(fields.values())) != len(fields)
    ):
        raise AttackRangeError("source_fields")
    expected_lengths = {
        "workshop_levels": 20,
        "current_levels": 20,
        "card_active": 40,
        "card_levels": 40,
        "primary_modules": 4,
        "assist_module_slots": 4,
        "research_levels": 250,
        "module_effects": 8,
    }
    lengths = contract["array_lengths"]
    if not isinstance(lengths, Mapping) or dict(lengths) != expected_lengths:
        raise AttackRangeError("array_lengths")
    expected_indices = {
        "workshop_range",
        "current_range",
        "range_card",
        "range_lab_research",
        "assist_substat_lab",
        "cannon_primary",
        "cannon_assist_type",
    }
    indices = contract["indices"]
    if (
        not isinstance(indices, Mapping)
        or set(indices) != expected_indices
        or any(
            _exact_int(value) is None or int(value) < 0
            for value in indices.values()
        )
    ):
        raise AttackRangeError("indices")
    if not (
        indices["workshop_range"] < lengths["workshop_levels"]
        and indices["current_range"] < lengths["current_levels"]
        and indices["range_card"] < lengths["card_active"]
        and indices["range_card"] < lengths["card_levels"]
        and indices["range_lab_research"] < lengths["research_levels"]
        and indices["assist_substat_lab"] < lengths["research_levels"]
        and indices["cannon_primary"] < lengths["primary_modules"]
        and indices["cannon_assist_type"] < lengths["assist_module_slots"]
    ):
        raise AttackRangeError("index_out_of_bounds")

    expected_bounds = {
        "workshop",
        "current",
        "selected_lab",
        "range_lab_research",
        "card",
        "assist_substat",
        "assist_substat_lab",
        "module_rarity",
        "module_level",
        "module_effect",
    }
    bounds = contract["level_bounds"]
    if not isinstance(bounds, Mapping) or set(bounds) != expected_bounds:
        raise AttackRangeError("level_bounds")
    for name, bound in bounds.items():
        if (
            not _is_sequence(bound)
            or len(bound) != 2
            or any(_exact_int(value) is None for value in bound)
            or int(bound[0]) < 0
            or int(bound[0]) > int(bound[1])
        ):
            raise AttackRangeError(f"level_bound_{name}")

    formula = contract["formula"]
    expected_formula = {
        "arithmetic",
        "base_game_units",
        "workshop_step_game_units",
        "lab_step_multiplier",
        "card_multipliers",
        "range_module_effect_game_units",
        "module_effect_slot_required_levels",
        "common_module_rarity",
        "assist_substat_efficiency_step",
        "compression",
        "display",
    }
    if not isinstance(formula, Mapping) or set(formula) != expected_formula:
        raise AttackRangeError("formula_shape")
    if formula["arithmetic"] != "v1073_v1101_il2cpp_binary32_v1":
        raise AttackRangeError("formula_arithmetic")
    for name in (
        "base_game_units",
        "workshop_step_game_units",
        "lab_step_multiplier",
        "assist_substat_efficiency_step",
    ):
        if _decimal(formula[name], name) <= 0:
            raise AttackRangeError(f"formula_{name}")
    multipliers = formula["card_multipliers"]
    if (
        not _is_sequence(multipliers)
        or len(multipliers) != bounds["card"][1] + 1
        or _decimal(multipliers[0], "card_multiplier") != 0
        or any(
            _decimal(value, "card_multiplier") < 1
            for value in multipliers[1:]
        )
    ):
        raise AttackRangeError("card_multipliers")
    range_effects = formula["range_module_effect_game_units"]
    if (
        not isinstance(range_effects, Mapping)
        or not range_effects
        or any(
            not str(key).isdigit() or _decimal(value, "module_effect") <= 0
            for key, value in range_effects.items()
        )
    ):
        raise AttackRangeError("range_module_effect_game_units")
    required = formula["module_effect_slot_required_levels"]
    if (
        not _is_sequence(required)
        or len(required) != 8
        or any(_exact_int(value) is None or int(value) < 0 for value in required)
    ):
        raise AttackRangeError("module_effect_slot_required_levels")
    if _exact_int(formula["common_module_rarity"]) is None:
        raise AttackRangeError("common_module_rarity")

    compression = formula["compression"]
    if not isinstance(compression, Mapping) or set(compression) != {
        "threshold_game_units",
        "cap_game_units",
        "span_divisor_game_units",
        "reduction_constant_binary32",
        "reduction_constant_bits",
    }:
        raise AttackRangeError("compression_shape")
    threshold = _decimal(
        compression["threshold_game_units"],
        "threshold_game_units",
    )
    cap = _decimal(compression["cap_game_units"], "cap_game_units")
    span_divisor = _decimal(
        compression["span_divisor_game_units"],
        "span_divisor_game_units",
    )
    reduction = _decimal(
        compression["reduction_constant_binary32"],
        "reduction_constant_binary32",
    )
    if not (
        0 < threshold < cap
        and span_divisor == threshold - cap
        and 0 < reduction < 1
        and compression["reduction_constant_bits"] == "0x3e23d70c"
        and _binary32_hex(reduction) == "0x3e23d70c"
    ):
        raise AttackRangeError("compression_values")
    display = formula["display"]
    if (
        not isinstance(display, Mapping)
        or set(display)
        != {"scale_to_meters", "decimal_places", "rounding", "unit"}
        or _decimal(display["scale_to_meters"], "scale_to_meters") != 10
        or display["decimal_places"] != 2
        or display["rounding"] != "dotnet_single_f2_half_up"
        or display["unit"] != "m"
    ):
        raise AttackRangeError("display_contract")
    return contract


def _authority(
    contract: Mapping[str, Any],
    *,
    semantic_fingerprint: str,
    binding_fingerprint: str,
    stable: bool,
    scope: str | None,
) -> dict[str, Any]:
    return {
        "kind": "versioned_calculation",
        "capability_id": contract["capability_id"],
        "audit_id": contract["audit_id"],
        "semantic_fingerprint": semantic_fingerprint,
        "binding_fingerprint": binding_fingerprint,
        "supported_game_versions": list(contract["supported_game_versions"]),
        "forward_policy": contract["forward_policy"],
        "scope": scope,
        "stable": stable,
    }


def _unavailable(
    source_fields: tuple[str, ...],
    reason: str,
    *,
    contract: Mapping[str, Any] | None = None,
    semantic_fingerprint: str = "",
    binding_fingerprint: str = "",
) -> AttackRangeEvidence:
    authority = (
        _authority(
            contract,
            semantic_fingerprint=semantic_fingerprint,
            binding_fingerprint=binding_fingerprint,
            stable=False,
            scope=None,
        )
        if contract is not None
        else {}
    )
    return AttackRangeEvidence(
        status="unmapped",
        value=None,
        source_fields=source_fields,
        complete=False,
        stable=False,
        scope=None,
        reason=reason,
        authority=authority,
        diagnostics={},
    )


def _declared_source_fields(raw: Any) -> tuple[str, ...]:
    fields = raw.get("source_fields") if isinstance(raw, Mapping) else None
    if not isinstance(fields, Mapping):
        return ()
    return tuple(
        dict.fromkeys(
            str(value)
            for value in fields.values()
            if isinstance(value, str) and value
        )
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _binary32(value: Any, label: str) -> float:
    decimal_value = _decimal(value, label)
    try:
        result = struct.unpack("!f", struct.pack("!f", float(decimal_value)))[0]
    except (OverflowError, struct.error) as exc:
        raise AttackRangeError(f"{label}_not_finite_binary32") from exc
    if not math.isfinite(result):
        raise AttackRangeError(f"{label}_not_finite_binary32")
    return result


def _binary32_hex(value: Any) -> str:
    packed = struct.pack("!f", float(value))
    return f"0x{struct.unpack('!I', packed)[0]:08x}"


def _f32_add(left: Any, right: Any) -> float:
    return _binary32(float(left) + float(right), "binary32_addition")


def _f32_sub(left: Any, right: Any) -> float:
    return _binary32(float(left) - float(right), "binary32_subtraction")


def _f32_mul(left: Any, right: Any) -> float:
    return _binary32(float(left) * float(right), "binary32_multiplication")


def _f32_div(left: Any, right: Any) -> float:
    if float(right) == 0:
        raise AttackRangeError("binary32_division_by_zero")
    return _binary32(float(left) / float(right), "binary32_division")


def _float_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise AttackRangeError(f"{label}_not_finite_decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AttackRangeError(f"{label}_not_finite_decimal") from exc
    if not result.is_finite():
        raise AttackRangeError(f"{label}_not_finite_decimal")
    return result


def _require_level(label: str, value: Any, bounds: Sequence[int]) -> None:
    if _exact_int(value) is None or not int(bounds[0]) <= value <= int(bounds[1]):
        raise AttackRangeError(f"{label}_outside_domain")


def _required_int(value: Any, label: str) -> int:
    result = _exact_int(value)
    if result is None:
        raise AttackRangeError(f"{label}_not_exact_integer")
    return result


def _indexed_int(values: Sequence[Any], index: int, label: str) -> int:
    try:
        value = values[index]
    except IndexError as exc:
        raise AttackRangeError(f"{label}_index_out_of_bounds") from exc
    return _required_int(value, label)


def _exact_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _exact_sequence(value: Any, length: int, label: str) -> list[Any]:
    if not _is_sequence(value) or len(value) != length:
        raise AttackRangeError(f"{label}_shape_changed")
    return list(value)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


__all__ = [
    "ATTACK_RANGE_CAPABILITY_ID",
    "AttackRangeCalculation",
    "AttackRangeError",
    "AttackRangeEvidence",
    "AttackRangeInputs",
    "attack_range_contract_fingerprints",
    "calculate_effective_attack_range",
    "effective_attack_range_from_save",
]
