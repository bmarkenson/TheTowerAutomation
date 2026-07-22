from pathlib import Path

import cv2
import numpy as np

from core.perk_configuration import parse_perk_configuration_selection


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


def test_auto_pick_order_keeps_dark_selected_rows_and_deduplicates_pages():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    dark = cv2.cvtColor(np.uint8([[[121, 157, 83]]]), cv2.COLOR_HSV2BGR)[0, 0]
    bright = cv2.cvtColor(np.uint8([[[121, 123, 137]]]), cv2.COLOR_HSV2BGR)[0, 0]
    frame[500:670, 107:973] = dark
    frame[700:870, 107:973] = dark
    frame[900:1070, 107:973] = bright
    texts = iter(
        [
            ("Perk Wave Requirement", 95.0),
            ("Coins", 94.0),
            ("Damage", 93.0),
            ("Perk Wave Requirement", 95.0),
            ("Coins", 94.0),
            ("Damage", 93.0),
        ]
    )

    result = parse_perk_configuration_selection(
        [frame, frame],
        field="perk_auto_pick_order",
        source_complete=True,
        source_reason="edge_reached",
        text_fn=lambda _crop: next(texts),
    )

    assert result["quality"]["valid"] is True
    assert result["order_semantics"] == "top_to_bottom_priority"
    assert [item["display_text"] for item in result["selected"]] == [
        "Perk Wave Requirement",
        "Coins",
    ]
    assert [item["rank"] for item in result["selected"]] == [1, 2]
    assert all(len(item["observations"]) == 2 for item in result["selected"])


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
