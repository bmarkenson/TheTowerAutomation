from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.battle_perks import ocr_perk_configuration_rows
from core.perk_configuration import (
    FARM_AUTO_PICK_ORDER,
    FARM_PERK_BANS,
    classify_perk_configuration_text,
    detect_auto_pick_ranking_boundary,
    evaluate_profile_perk_configuration,
    extract_configured_perk_bans,
    extract_ranked_auto_pick_order,
    parse_perk_configuration_selection,
    semantic_perk_entry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "test"
    / "fixtures"
    / "ui_state_20260714"
    / "no_battle_perks_configuration_20260719.png"
)


def test_first_perk_fixture_separates_selected_row_from_available_rows():
    frame = cv2.imread(str(FIXTURE))
    assert frame is not None

    result = parse_perk_configuration_selection(
        [frame],
        field="perk_first_choice",
        source_complete=True,
        source_reason="edge_reached",
    )

    assert result["quality"]["valid"] is True
    assert result["order_semantics"] == "single_choice"
    assert [item["display_text"] for item in result["selected"]] == [
        "Perk wave requirement -25.00%"
    ]
    assert result["raw_pages"][0]["rows"][0]["background_value_median"] < 110
    assert result["raw_pages"][0]["rows"][1]["background_value_median"] > 110


def test_auto_pick_observation_reads_all_category_colors_until_rank_boundary():
    frames = [
        np.zeros((1920, 1080, 3), dtype=np.uint8),
        np.zeros((1920, 1080, 3), dtype=np.uint8),
    ]
    pages = [
        [
            _configuration_row("Perk wave requirement -25.00%", 500, 657, 137),
            _configuration_row("Increase max game speed by +1.25", 700, 857, 96),
        ],
        [
            _configuration_row("Increase max game speed by +1.25", 500, 657, 96),
            _configuration_row(
                "x1.98 coins, but tower max health -70.0%",
                700,
                857,
                130,
            ),
            _configuration_row("x1.44 Damage", 1300, 1457, 137),
        ],
    ]

    with (
        patch(
            "core.perk_configuration.ocr_perk_configuration_rows",
            side_effect=pages,
        ),
        patch(
            "core.perk_configuration.detect_auto_pick_ranking_boundary",
            side_effect=[None, 1200],
        ),
    ):
        result = parse_perk_configuration_selection(
            frames,
            field="perk_auto_pick_order",
            source_complete=True,
            source_reason="edge_reached",
        )

    assert result["quality"]["valid"] is True
    assert result["quality"]["ranking_boundary_seen"] is True
    assert result["order_semantics"] == "top_to_bottom_priority"
    assert [item["key"] for item in result["selected"]] == [
        "perk_wave_requirement",
        "game_speed",
        "coin_tradeoff",
    ]
    assert [item["rank"] for item in result["selected"]] == [1, 2, 3]
    assert len(result["selected"][1]["observations"]) == 2


def test_ban_observation_uses_selected_outlines_not_category_brightness():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    rows = [
        {
            **_configuration_row(text, 500 + index * 100, 590 + index * 100, value),
            "selected_outline": selected,
        }
        for index, (text, value, selected) in enumerate(
            (
                ("Lifesteal x2.75, but knockback force -70%", 82, True),
                ("Enemies damage -55.0%, but tower damage -50%", 82, True),
                ("x1.44 Defense Absolute", 83, True),
                ("Interest x1.88", 83, True),
                ("Land Mine Damage x4.38", 83, True),
                ("Swamp radius x1.5", 32, True),
                ("Boss health -73.5%, but boss speed +50%", 82, False),
            )
        )
    ]

    with patch(
        "core.perk_configuration.ocr_perk_configuration_rows",
        return_value=rows,
    ):
        result = parse_perk_configuration_selection(
            [frame],
            field="perk_bans",
            source_complete=True,
            source_reason="edge_reached",
        )

    assert result["quality"]["valid"] is True
    assert [item["key"] for item in result["selected"]] == [
        "lifesteal_knockback_tradeoff",
        "enemies_damage_tradeoff",
        "defense_absolute",
        "interest",
        "land_mine_damage",
        "swamp_radius",
    ]


def test_configuration_row_scanner_recovers_dark_outlined_green_row():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[500:680, 107:973] = (0, 32, 0)
    frame[500:507, 107:973] = 255
    frame[673:680, 107:973] = 255

    rows = ocr_perk_configuration_rows(
        frame,
        text_fn=lambda _crop: ("Swamp radius x1.5", 95.0),
    )

    assert len(rows) == 1
    assert rows[0]["selected_outline"] is True
    assert rows[0]["background_value_median"] < 55
    assert semantic_perk_entry(rows[0])["key"] == "swamp_radius"


def test_auto_pick_ranking_boundary_requires_paired_horizontal_rules():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[1246:1261, 125:297] = 255
    assert detect_auto_pick_ranking_boundary(frame) is None

    frame[1246:1261, 784:956] = 255
    assert detect_auto_pick_ranking_boundary(frame) == 1253


def test_ranked_auto_pick_capture_does_not_fill_a_missed_rank_from_below_boundary():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[1246:1261, 125:297] = 255
    frame[1246:1261, 784:956] = 255
    rows = [
        {
            "top": 500,
            "bottom": 657,
            "display_text": "Perk wave requirement -25.00%",
            "text_raw": "Perk wave requirement -25.00%",
            "confidence": 95.0,
            "background_value_median": 100.0,
        },
        {
            "top": 700,
            "bottom": 857,
            "display_text": "Increase max game speed by +1.25",
            "text_raw": "Increase max game speed by +1.25",
            "confidence": 95.0,
            "background_value_median": 100.0,
        },
        {
            "top": 1300,
            "bottom": 1457,
            "display_text": "Extra set of inner mines",
            "text_raw": "Extra set of inner mines",
            "confidence": 95.0,
            "background_value_median": 79.0,
        },
    ]

    result = extract_ranked_auto_pick_order(
        [frame],
        ranking_count=3,
        row_fn=lambda _frame: rows,
    )

    assert [item["key"] for item in result["selected"]] == [
        "perk_wave_requirement",
        "game_speed",
    ]
    assert result["quality"]["valid"] is False
    assert result["quality"]["ranking_boundary_seen"] is True
    assert result["raw_pages"][0]["ranking_boundary_y"] == 1253
    assert result["quality"]["warnings"] == [
        "Auto Pick exposed 2 of 3 ranked perks before the ranking boundary"
    ]


def test_ban_capture_accepts_two_low_confidence_candidates_with_same_semantics():
    rows = [
        {
            "top": 430 + index * 172,
            "bottom": 587 + index * 172,
            "display_text": text,
            "text_raw": text,
            "confidence": confidence,
            "background_value_median": 83.0,
            "text_candidates": [
                {
                    "display_text": text,
                    "text_raw": text,
                    "confidence": confidence,
                    "text_x1": text_x1,
                }
                for text_x1 in (270, 400)
            ],
        }
        for index, (text, confidence) in enumerate(
            (
                ("Lifesteal x2.75, but knockback force -70%", 90.0),
                ("Enemies damage -55.0%, but tower damage -50%", 90.0),
                ("x1.44 Defense Absolute", 90.0),
                ("Interest x1.88", 90.0),
                ("Land Mine Damage x4.38", 90.0),
                ("1.44 Cash Bonus", 63.3),
            )
        )
    ]

    result = extract_configured_perk_bans(
        np.zeros((1920, 1080, 3), dtype=np.uint8),
        row_fn=lambda _frame: rows,
    )

    assert result["quality"]["valid"] is True
    assert result["quality"]["low_confidence"] == []
    assert result["selected"][-1]["key"] == "cash_bonus"
    assert result["selected"][-1]["semantic_agreement"] == 2

    rows[-1]["text_candidates"] = rows[-1]["text_candidates"][:1]
    unconfirmed = extract_configured_perk_bans(
        np.zeros((1920, 1080, 3), dtype=np.uint8),
        row_fn=lambda _frame: rows,
    )
    assert unconfirmed["quality"]["valid"] is False
    assert unconfirmed["quality"]["low_confidence"] == ["1.44 Cash Bonus"]


def test_empty_ban_list_is_a_valid_observed_configuration():
    result = parse_perk_configuration_selection(
        [np.zeros((1920, 1080, 3), dtype=np.uint8)],
        field="perk_bans",
        source_complete=True,
        source_reason="edge_reached",
    )

    assert result["selected"] == []
    assert result["quality"]["valid"] is True


def test_incomplete_capture_retains_rows_but_is_not_authoritative():
    frame = cv2.imread(str(FIXTURE))

    result = parse_perk_configuration_selection(
        [frame],
        field="perk_first_choice",
        source_complete=False,
        source_reason="max_swipes_exceeded",
    )

    assert result["selected"]
    assert result["quality"]["valid"] is False
    assert "incomplete" in result["quality"]["warnings"][0]


def test_configuration_row_scanner_includes_pale_blue_home_rows():
    frame = cv2.imread(str(FIXTURE))

    rows = ocr_perk_configuration_rows(frame)

    assert len(rows) >= 6
    assert semantic_perk_entry(rows[0])["key"] == (
        "perk_wave_requirement"
    )
    assert any(
        semantic_perk_entry(row)["key"] == "coins_bonus"
        for row in rows
    )


def test_auto_pick_rows_have_value_independent_semantic_keys():
    cases = {
        (
            "Enemies have -55.0% health, "
            "but tower health regen and lifesteal -90%"
        ): "enemy_health_tradeoff",
        (
            "Boss health -73.5%, but boss speed +50%"
        ): "boss_health_tradeoff",
        (
            "x1.65 tower damage, but bosses have x8 health"
        ): "tower_damage_boss_health_tradeoff",
        "Bounce Shot +2": "bounce_shot",
        "4 more smart missiles": "smart_missiles",
        "Defense percent +5.00%": "defense_percent",
        "x1.25 max health": "max_health",
        "x2.19 Health Regen": "health_regen",
        (
            "tower health regen x8.80, but tower max health -60%"
        ): "health_regen_tradeoff",
        (
            "Enemies speed -44.0%, but enemies damage x2.5"
        ): "enemy_speed_tradeoff",
        (
            "Ranged enemies attack distance reduced, "
            "but ranged enemies damage x3"
        ): "ranged_distance_tradeoff",
        "Land Mine Damage x4.38": "land_mine_damage",
        "x1.44 Cash Bonus": "cash_bonus",
        "Swamp radius x1.5": "swamp_radius",
    }

    assert {
        text: classify_perk_configuration_text(text)
        for text in cases
    } == cases


def _configuration_row(
    text: str,
    top: int,
    bottom: int,
    background_value: float,
) -> dict:
    return {
        "top": top,
        "bottom": bottom,
        "display_text": text,
        "text_raw": text,
        "confidence": 95.0,
        "background_value_median": background_value,
        "selected_outline": False,
    }


def test_farm_auto_pick_post_economy_order_matches_17_slot_priority():
    assert FARM_AUTO_PICK_ORDER[8:] == (
        "orbs",
        "enemy_health_tradeoff",
        "tower_damage_boss_health_tradeoff",
        "enemy_speed_tradeoff",
        "boss_health_tradeoff",
        "ranged_distance_tradeoff",
        "chain_lightning_damage",
        "inner_land_mines",
        "damage",
    )


def test_farm_bans_use_all_six_slots_for_nonessential_farm_perks():
    assert FARM_PERK_BANS == (
        "lifesteal_knockback_tradeoff",
        "enemies_damage_tradeoff",
        "defense_absolute",
        "interest",
        "land_mine_damage",
        "cash_bonus",
    )


def test_farm_perk_configuration_requires_coin_tradeoff_at_rank_three():
    frames = [
        np.full((4, 4, 3), marker, dtype=np.uint8)
        for marker in (1, 2, 3, 4)
    ]
    labels = {
        "cash_tradeoff": "x13.20 cash per wave, but enemy kills don't give cash",
        "enemies_damage_tradeoff": (
            "Enemies damage -55.0%, but tower damage -50%"
        ),
        "lifesteal_knockback_tradeoff": (
            "Lifesteal x2.75, but knockback force -70%"
        ),
        "interest": "Interest x1.88",
        "defense_absolute": "x1.44 Defense Absolute",
        "land_mine_damage": "Land Mine Damage x4.38",
        "cash_bonus": "x1.44 Cash Bonus",
        "empty_slot": "Empty Slot",
        "perk_wave_requirement": "Perk wave requirement -25.00%",
        "game_speed": "Increase max game speed by +1.25",
        "coin_tradeoff": "x1.98 coins, but tower max health -70.0%",
        "golden_tower_bonus": "Golden tower bonus x1.5",
        "black_hole_duration": "Black Hole duration +12.0s",
        "death_wave_quantity": "+1 wave on death wave",
        "coins_bonus": "x1.44 all coins bonuses",
        "free_upgrade_chance": "Free upgrade chance for all +6.25%",
        "enemy_health_tradeoff": (
            "Enemies have -55.0% health, "
            "but tower health regen and lifesteal -90%"
        ),
        "boss_health_tradeoff": (
            "Boss health -73.5%, but boss speed +50%"
        ),
        "tower_damage_boss_health_tradeoff": (
            "x1.65 tower damage, but bosses have x8 health"
        ),
        "defense_percent": "Defense percent +5.00%",
        "max_health": "x1.25 max health",
        "health_regen": "x2.19 Health Regen",
        "health_regen_tradeoff": (
            "tower health regen x8.80, but tower max health -60%"
        ),
        "enemy_speed_tradeoff": (
            "Enemies speed -44.0%, but enemies damage x2.5"
        ),
        "ranged_distance_tradeoff": (
            "Ranged enemies attack distance reduced, "
            "but ranged enemies damage x3"
        ),
        "orbs": "Orbs +1",
        "chain_lightning_damage": "Chain lightning damage x2",
        "inner_land_mines": "Extra set of inner mines",
        "spotlight_damage": "Spotlight damage bonus x1.5",
        "damage": "x1.44 Damage",
    }

    def rows(keys):
        return [
            {
                "top": 430 + index * 172,
                "bottom": 587 + index * 172,
                "display_text": labels[key],
                "text_raw": labels[key],
                "confidence": 95.0,
                "background_value_median": 100.0,
            }
            for index, key in enumerate(keys)
        ]

    correct = {
        1: rows([*FARM_PERK_BANS, "empty_slot"]),
        2: rows(FARM_AUTO_PICK_ORDER[:8]),
        3: rows(FARM_AUTO_PICK_ORDER[5:13]),
        4: rows(FARM_AUTO_PICK_ORDER[10:]),
    }

    def row_fn(frame):
        return correct[int(frame[0, 0, 0])]

    requirements = {
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
    }
    result = evaluate_profile_perk_configuration(
        requirements,
        bans_frame=frames[0],
        auto_pick_frames=frames[1:],
        row_fn=row_fn,
    )
    assert result["valid"] is True
    assert result["perk_auto_pick_order"]["observed"][2] == "coin_tradeoff"

    missing_cto = dict(correct)
    changed_order = list(FARM_AUTO_PICK_ORDER)
    changed_order.remove("coin_tradeoff")
    changed_order.append("coin_tradeoff")
    missing_cto[2] = rows(changed_order[:8])
    missing_cto[3] = rows(changed_order[5:13])
    missing_cto[4] = rows(changed_order[10:])

    def changed_row_fn(frame):
        return missing_cto[int(frame[0, 0, 0])]

    result = evaluate_profile_perk_configuration(
        requirements,
        bans_frame=frames[0],
        auto_pick_frames=frames[1:],
        row_fn=changed_row_fn,
    )
    assert result["valid"] is False
    assert result["failed_checks"] == ["perk_auto_pick_order"]
    assert result["perk_auto_pick_order"]["observed"][2] != "coin_tradeoff"
    assert result["perk_auto_pick_order"]["observed"][-1] == "coin_tradeoff"
