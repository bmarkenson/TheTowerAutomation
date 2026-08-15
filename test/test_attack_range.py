from __future__ import annotations

import copy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from core.attack_range import (
    AttackRangeError,
    AttackRangeInputs,
    attack_range_contract_fingerprints,
    calculate_effective_attack_range,
    effective_attack_range_from_save,
)


ROOT = Path(__file__).resolve().parents[1]
MAPPING = json.loads(
    (ROOT / "config/player_save_versions/data_9_game_1101.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT = MAPPING["effective_attack_range"]


def _assist_slot(
    slot_type: int,
    *,
    unlocked: bool = False,
    module: dict | None = None,
    substat_level: int = 0,
) -> dict:
    return {
        "__class__": "AssistModuleSlot",
        "type": slot_type,
        "unlocked": unlocked,
        "equippedModule": module,
        "substatEfficiencyLevel": substat_level,
    }


def _module(
    *,
    effect_id: int,
    slot: int = 0,
    rarity: int = 15,
    level: int = 300,
) -> dict:
    effects = [0] * 8
    effects[slot] = effect_id
    return {
        "__class__": "ModuleItem",
        "infoIndex": 1,
        "currentRarity": rarity,
        "level": level,
        "effects": effects,
    }


def _decoded(
    *,
    workshop_level: int = 79,
    current_level: int = 79,
    lab_level: int = 0,
    card_active: bool = True,
    card_level: int = 7,
) -> dict:
    workshop = [0] * 20
    current = [0] * 20
    cards_active = [False] * 40
    cards = [0] * 40
    workshop[4] = workshop_level
    current[4] = current_level
    cards_active[4] = card_active
    cards[4] = card_level
    return {
        "versionNumber": 1101,
        "roundActiveBool": True,
        "upgradeWorkshopLevel": workshop,
        "upgradeLevel": current,
        "rangeLevelSelected": lab_level,
        "cardActive": cards_active,
        "cardLevel": cards,
        "moduleEquipped": [None] * 4,
        "assistModuleSlots": [_assist_slot(index) for index in range(4)],
        "researchLevel": [0] * 250,
    }


def test_base_range_uses_current_total_level_not_base_plus_current():
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=40,
            current_level=50,
            selected_lab_level=0,
            range_card_active=False,
            range_card_level=0,
        ),
        CONTRACT,
    )

    assert calculated.workshop_level_base_meters == Decimal("50.0")
    assert calculated.in_battle_level_delta_meters == Decimal("5.0")
    assert calculated.pre_compression_meters == Decimal("55.0")
    assert calculated.display_value == "55.00m"


@pytest.mark.parametrize(
    ("card_level", "multiplier"),
    (
        (1, "1.15"),
        (2, "1.20"),
        (3, "1.25"),
        (4, "1.30"),
        (5, "1.35"),
        (6, "1.40"),
        (7, "1.45"),
    ),
)
def test_selected_lab_and_each_active_range_card_level_are_applied(
    card_level,
    multiplier,
):
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=0,
            current_level=0,
            selected_lab_level=10,
            range_card_active=True,
            range_card_level=card_level,
        ),
        CONTRACT,
    )

    assert float(calculated.lab_multiplier) == pytest.approx(1.20)
    assert float(calculated.card_multiplier) == pytest.approx(float(multiplier))
    assert float(calculated.pre_compression_meters) == pytest.approx(
        30 * 1.20 * float(multiplier),
        abs=0.00001,
    )


def test_inactive_range_card_does_not_index_its_locked_level():
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=0,
            current_level=0,
            selected_lab_level=0,
            range_card_active=False,
            range_card_level=0,
        ),
        CONTRACT,
    )

    assert calculated.card_multiplier == 1
    assert calculated.display_value == "30.00m"


def test_retained_tournament_vector_calculates_98_38_meters():
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=79,
            current_level=79,
            selected_lab_level=0,
            range_card_active=True,
            range_card_level=7,
        ),
        CONTRACT,
    )

    assert float(calculated.pre_compression_meters) == pytest.approx(
        100.775,
        abs=0.00001,
    )
    assert float(calculated.effective_meters) == pytest.approx(
        98.38232,
        abs=0.00001,
    )
    assert calculated.display_value == "98.38m"


def test_display_uses_dotnet_f2_half_up_after_binary32_math():
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=0,
            current_level=0,
            selected_lab_level=0,
            range_card_active=False,
            range_card_level=0,
            module_bonus_game_units=Decimal("0.0125"),
        ),
        CONTRACT,
    )

    assert calculated.effective_meters == Decimal("30.125")
    assert calculated.display_value == "30.13m"


@pytest.mark.parametrize(
    ("module_bonus", "pre_compression", "effective", "display"),
    (
        ("5", "80", "80.00", "80.00m"),
        ("19", "220", "184.800", "184.80m"),
        ("20", "230", "193.20", "193.20m"),
    ),
)
def test_nonlinear_compression_boundaries(
    module_bonus,
    pre_compression,
    effective,
    display,
):
    calculated = calculate_effective_attack_range(
        AttackRangeInputs(
            workshop_level=0,
            current_level=0,
            selected_lab_level=0,
            range_card_active=False,
            range_card_level=0,
            module_bonus_game_units=Decimal(module_bonus),
        ),
        CONTRACT,
    )

    assert float(calculated.pre_compression_meters) == pytest.approx(
        float(pre_compression)
    )
    assert float(calculated.effective_meters) == pytest.approx(float(effective))
    assert calculated.display_value == display


def test_primary_and_assist_cannon_range_effects_are_included():
    decoded = _decoded(card_active=False, card_level=0)
    decoded["moduleEquipped"][0] = _module(effect_id=19)
    decoded["assistModuleSlots"][0] = _assist_slot(
        0,
        unlocked=True,
        module=_module(effect_id=23),
        substat_level=4,
    )
    decoded["researchLevel"][230] = 10

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    # Primary +2m; assist +20m at (10 + 4 + 1)% contributes +3m.
    assert evidence.status == "observed"
    assert evidence.complete is True
    assert evidence.diagnostics["module_bonus_meters"] == "5.0"
    assert evidence.diagnostics["pre_compression_meters"] == "74.5"
    assert evidence.value == "74.50m"


def test_module_effect_slot_level_and_common_rarity_gates_are_applied():
    below_level = _decoded(card_active=False, card_level=0)
    below_level["moduleEquipped"][0] = _module(
        effect_id=24,
        slot=7,
        level=240,
    )
    common_later_slot = _decoded(card_active=False, card_level=0)
    common_later_slot["moduleEquipped"][0] = _module(
        effect_id=24,
        slot=1,
        rarity=1,
    )
    active = _decoded(card_active=False, card_level=0)
    active["moduleEquipped"][0] = _module(
        effect_id=24,
        slot=7,
        level=241,
    )

    assert effective_attack_range_from_save(below_level, MAPPING).value == (
        "69.50m"
    )
    assert effective_attack_range_from_save(common_later_slot, MAPPING).value == (
        "69.50m"
    )
    assert effective_attack_range_from_save(active, MAPPING).value == "97.28m"


def test_nonnull_module_info_index_zero_still_uses_its_range_effect():
    decoded = _decoded(card_active=False, card_level=0)
    decoded["moduleEquipped"][0] = _module(effect_id=19)
    decoded["moduleEquipped"][0]["infoIndex"] = 0

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "observed"
    assert evidence.diagnostics["module_bonus_meters"] == "2.0"
    assert evidence.value == "71.50m"


@pytest.mark.parametrize(
    ("effects", "reason_fragment"),
    (
        ([332] + [0] * 7, "effects_outside_domain"),
        ([19, 20] + [0] * 6, "duplicate_range_effect_cluster"),
    ),
)
def test_malformed_module_effect_domains_fail_closed(effects, reason_fragment):
    decoded = _decoded(card_active=False, card_level=0)
    decoded["moduleEquipped"][0] = _module(effect_id=0)
    decoded["moduleEquipped"][0]["effects"] = effects

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "unmapped"
    assert reason_fragment in evidence.reason


def test_nonmax_current_level_is_diagnostic_but_not_shortcut_authority():
    decoded = _decoded(workshop_level=20, current_level=30)

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "observed"
    assert evidence.value == "65.25m"
    assert evidence.stable is False
    assert evidence.complete is False
    assert evidence.reason == "effective_attack_range_can_still_upgrade"
    assert evidence.diagnostics["in_battle_range_upgrades"] == 10


def test_exact_v1073_uses_the_same_binary_proved_contract():
    decoded = _decoded()
    decoded["versionNumber"] = 1073

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "observed"
    assert evidence.complete is True
    assert evidence.value == "98.38m"


def test_out_of_round_range_uses_workshop_level_and_is_reusable():
    decoded = _decoded(workshop_level=20, current_level=0)
    decoded["roundActiveBool"] = False
    decoded["cardActive"][4] = False
    decoded["cardLevel"][4] = 0

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "observed"
    assert evidence.complete is True
    assert evidence.stable is True
    assert evidence.scope == "configured_out_of_round"
    assert evidence.value == "40.00m"
    assert evidence.diagnostics["level_source"] == "upgradeWorkshopLevel"


def test_selected_range_lab_cannot_exceed_researched_level():
    decoded = _decoded(lab_level=10)
    decoded["researchLevel"][3] = 9

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "unmapped"
    assert "selected_range_lab_exceeds_researched_level" in evidence.reason


@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    (
        (
            lambda decoded: decoded.update(versionNumber=1102),
            "game_version_unsupported",
        ),
        (
            lambda decoded: decoded.update(upgradeLevel=[0] * 19),
            "current_levels_shape_changed",
        ),
        (
            lambda decoded: decoded["upgradeLevel"].__setitem__(4, True),
            "current_level_not_exact_integer",
        ),
        (
            lambda decoded: decoded["cardActive"].__setitem__(4, 1),
            "range_card_active_not_exact_boolean",
        ),
        (
            lambda decoded: decoded["cardLevel"].__setitem__(4, 0),
            "active_range_card_has_no_unlocked_level",
        ),
        (
            lambda decoded: decoded.update(assistModuleSlots=[]),
            "assist_module_slots_shape_changed",
        ),
    ),
)
def test_unsupported_or_malformed_save_dependencies_fail_closed(
    mutation,
    reason_fragment,
):
    decoded = _decoded()
    mutation(decoded)

    evidence = effective_attack_range_from_save(decoded, MAPPING)

    assert evidence.status == "unmapped"
    assert evidence.value is None
    assert evidence.complete is False
    assert evidence.stable is False
    assert reason_fragment in evidence.reason


def test_active_range_card_level_zero_is_rejected_by_pure_calculator():
    with pytest.raises(
        AttackRangeError,
        match="active_range_card_has_no_unlocked_level",
    ):
        calculate_effective_attack_range(
            AttackRangeInputs(
                workshop_level=0,
                current_level=0,
                selected_lab_level=0,
                range_card_active=True,
                range_card_level=0,
            ),
            CONTRACT,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda contract: contract["formula"].update(base_game_units="3.1"),
        lambda contract: contract["formula"]["card_multipliers"].__setitem__(
            7,
            "1.46",
        ),
        lambda contract: contract["formula"]["compression"].update(
            reduction_constant_binary32="0.17"
        ),
        lambda contract: contract["formula"]["display"].update(
            rounding="half_even"
        ),
    ),
)
def test_semantic_fingerprint_binds_every_formula_stage(mutation):
    changed = copy.deepcopy(CONTRACT)
    mutation(changed)

    original_semantic, original_binding = attack_range_contract_fingerprints(
        CONTRACT
    )
    try:
        changed_semantic, changed_binding = attack_range_contract_fingerprints(
            changed
        )
    except AttackRangeError:
        # Unsupported semantics are fail-closed rather than silently retaining
        # the old contract fingerprint.
        return

    assert changed_semantic != original_semantic
    assert changed_binding == original_binding


def test_binding_fingerprint_changes_when_a_raw_index_changes():
    changed = copy.deepcopy(CONTRACT)
    changed["indices"]["range_card"] = 5

    original_semantic, original_binding = attack_range_contract_fingerprints(
        CONTRACT
    )
    changed_semantic, changed_binding = attack_range_contract_fingerprints(
        changed
    )

    assert changed_semantic == original_semantic
    assert changed_binding != original_binding
