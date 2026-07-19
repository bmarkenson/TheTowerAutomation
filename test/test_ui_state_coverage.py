from pathlib import Path

import cv2
import pytest

from core.state_detector import detect_state_and_overlays
from core.label_tapper import get_label_match


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


def test_tournament_heat_has_a_dedicated_visible_close_control():
    screenshot = cv2.imread(str(FIXTURES / "active_tournament_heat_20260718.png"))

    assert screenshot is not None
    x, y, w, h = get_label_match(
        "buttons.close:tournament_heat",
        screenshot=screenshot,
    )
    assert (x + w // 2, y + h // 2) == (982, 180)
