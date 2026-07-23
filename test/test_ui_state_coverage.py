from pathlib import Path
from unittest.mock import patch

import cv2
import pytest

from core.state_detector import detect_state_and_overlays
from core.label_tapper import get_label_match
from core.ss_capture import normalize_device_screenshot


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ui_state_20260714"


@pytest.mark.parametrize(
    ("fixture_name", "expected_state", "expected_overlays"),
    [
        ("active_wave_info.png", "WAVE_PANEL", {"MENU_OPEN"}),
        ("active_wave_stats.png", "WAVE_PANEL", {"MENU_OPEN"}),
        ("active_perks_selected_auto_pick_on.png", "PERKS", set()),
        ("no_battle_perks_configuration_20260719.png", "PERKS", set()),
        ("no_battle_milestones_20260719.png", "MILESTONES", set()),
        (
            "no_battle_tournament_currencies_20260719.png",
            "TOURNAMENT_SCREEN",
            {"CURRENCIES_DIALOG"},
        ),
        (
            "no_battle_milestones_exit_game_20260719.png",
            "MILESTONES",
            {"EXIT_GAME_DIALOG"},
        ),
        ("active_settings_stats.png", "SETTINGS", {"MENU_OPEN"}),
        ("active_settings_toggles.png", "SETTINGS", {"MENU_OPEN"}),
        ("active_settings_language.png", "SETTINGS", {"MENU_OPEN"}),
        ("active_settings_update_notes.png", "SETTINGS", set()),
        ("active_settings_credits.png", "SETTINGS", {"MENU_OPEN"}),
        ("home_settings_encyclopedia.png", "SETTINGS", set()),
        ("active_lab_select_research.png", "LAB", set()),
        ("active_lab_history.png", "LAB", set()),
        ("active_modules_information.png", "MODULES", set()),
        ("active_modules_history.png", "MODULES", set()),
        ("active_battle_heat.png", "BATTLE_HEAT", set()),
        ("active_battle_overheat.png", "BATTLE_HEAT", set()),
        ("active_tournament_heat_20260718.png", "BATTLE_HEAT", set()),
        ("active_battle_history.png", "BATTLE_HISTORY", set()),
        ("active_battle_history_detail.png", "BATTLE_HISTORY", set()),
        (
            "active_exit_battle_dialog.png",
            "EXIT_BATTLE_DIALOG",
            {"EXIT_BATTLE_DIALOG", "MENU_OPEN"},
        ),
        (
            "active_upgrade_detail_health.png",
            "UPGRADE_DETAIL",
            {"UPGRADE_DETAIL", "MENU_OPEN"},
        ),
        (
            "active_uw_detail_chain_lightning.png",
            "UPGRADE_DETAIL",
            {"UPGRADE_DETAIL", "MENU_OPEN"},
        ),
        (
            "active_attack_upgrade_detail.png",
            "DAMAGE_ADJUSTER",
            {"UPGRADE_DETAIL", "MENU_OPEN"},
        ),
        (
            "active_buy_quantity_expanded.png",
            "RUNNING",
            {"BUY_QUANTITY_MENU_EXPANDED", "MENU_OPEN"},
        ),
        ("active_distance_adjuster.png", "DISTANCE_ADJUSTER", {"MENU_OPEN"}),
        ("home_inbox_mail.png", "INBOX", set()),
        ("home_inbox_news.png", "INBOX", set()),
        ("home_ranking_direct.png", "RANKING", set()),
        ("home_profile_themes.png", "THEMES", set()),
        ("home_vault_harmony.png", "VAULT", set()),
        ("home_vault_power.png", "VAULT", set()),
        ("home_tournament.png", "TOURNAMENT_SCREEN", set()),
        ("home_tournament_info.png", "TOURNAMENT_SCREEN", set()),
        ("home_tournament_history.png", "TOURNAMENT_SCREEN", set()),
        ("home_tournament_prizes_direct.png", "TOURNAMENT_SCREEN", set()),
    ],
)
def test_traversal_fixture_has_explicit_state_and_overlays(
    fixture_name: str,
    expected_state: str,
    expected_overlays: set[str],
):
    screenshot = cv2.imread(str(FIXTURES / fixture_name))
    assert screenshot is not None, f"fixture is unreadable: {fixture_name}"

    detection = detect_state_and_overlays(screenshot)

    assert detection["state"] == expected_state
    assert expected_overlays <= set(detection["overlays"])

    source_720p = cv2.resize(
        screenshot,
        (720, 1280),
        interpolation=cv2.INTER_AREA,
    )
    normalized = normalize_device_screenshot(
        source_720p,
        device_id="fixture:720p",
    )
    assert normalized is not None
    detection_720p = detect_state_and_overlays(normalized)
    assert detection_720p["state"] == expected_state
    assert expected_overlays <= set(detection_720p["overlays"])


def test_game_over_can_fall_back_to_its_more_stats_button():
    definitions = {
        "states": [
            {
                "name": "GAME_OVER",
                "type": "primary",
                "match_keys": [
                    "indicators.game_over",
                    "buttons.more_stats:game_over",
                ],
            }
        ],
        "overlays": [],
    }
    frame = cv2.imread(
        str(
            Path(__file__).resolve().parent
            / "fixtures"
            / "game_over_stats_20260715.png"
        )
    )
    assert frame is not None

    def match(key, *, screenshot):
        del screenshot
        if key == "buttons.more_stats:game_over":
            return (192, 968), 0.97
        return None, 0.76

    with patch("core.state_detector.get_match", side_effect=match):
        detection = detect_state_and_overlays(frame, state_defs=definitions)

    assert detection["state"] == "GAME_OVER"


def test_background_running_evidence_yields_to_perks_modal():
    definitions = {
        "states": [
            {
                "name": "RUNNING",
                "type": "background_primary",
                "match_keys": ["indicators.cinematic_wall_icon"],
            },
            {
                "name": "PERKS",
                "type": "primary",
                "match_keys": ["indicators.perks_panel"],
            },
        ],
        "overlays": [],
    }
    frame = cv2.imread(
        str(FIXTURES / "active_perks_selected_auto_pick_on.png")
    )
    assert frame is not None

    with patch(
        "core.state_detector.get_match",
        return_value=((44, 1620), 1.0),
    ):
        detection = detect_state_and_overlays(frame, state_defs=definitions)

    assert detection["state"] == "PERKS"


def test_background_running_evidence_is_used_without_a_specific_state():
    definitions = {
        "states": [
            {
                "name": "RUNNING",
                "type": "background_primary",
                "match_keys": ["indicators.cinematic_wall_icon"],
            }
        ],
        "overlays": [],
    }
    frame = cv2.imread(
        str(FIXTURES / "active_perks_selected_auto_pick_on.png")
    )
    assert frame is not None

    with patch(
        "core.state_detector.get_match",
        return_value=((44, 1620), 1.0),
    ):
        detection = detect_state_and_overlays(frame, state_defs=definitions)

    assert detection["state"] == "RUNNING"


def test_tournament_heat_has_a_dedicated_visible_close_control():
    screenshot = cv2.imread(str(FIXTURES / "active_tournament_heat_20260718.png"))

    assert screenshot is not None
    x, y, w, h = get_label_match(
        "buttons.close:tournament_heat",
        screenshot=screenshot,
    )
    assert (x + w // 2, y + h // 2) == (982, 180)
