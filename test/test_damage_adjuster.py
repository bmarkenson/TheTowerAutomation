from pathlib import Path
from unittest.mock import patch

import cv2

from core.clickmap_access import get_click
from core.damage_adjuster import (
    DAMAGE_SELECTOR_MODE,
    dismiss_damage_adjuster,
    open_damage_adjuster,
    read_damage_adjuster,
)
from core.input import tap_if_visible
from core.state_detector import detect_state_and_overlays


ROOT = Path(__file__).resolve().parents[1]
PANEL_FIXTURE = ROOT / "test" / "fixtures" / "damage_adjuster_1e22_20260714.png"
ATTACK_FIXTURE = ROOT / "test" / "fixtures" / "attack_menu_damage_max_20260714.png"


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def test_live_damage_adjuster_fixture_reads_configurable_percentage():
    screen = _load(PANEL_FIXTURE)

    detection = detect_state_and_overlays(screen)
    reading = read_damage_adjuster(screen)

    assert detection["state"] == "DAMAGE_ADJUSTER"
    assert detection["menu"] is None
    assert reading.visible
    assert reading.mode == DAMAGE_SELECTOR_MODE
    assert reading.percentage == "1E-22%"
    assert reading.panel_confidence >= 0.99
    assert reading.ocr_confidence >= 90


def test_closed_attack_menu_does_not_match_damage_adjuster():
    screen = _load(ATTACK_FIXTURE)

    detection = detect_state_and_overlays(screen)
    reading = read_damage_adjuster(screen)

    assert detection["state"] == "RUNNING"
    assert detection["menu"] == "ATTACK_MENU"
    assert not reading.visible
    assert reading.percentage is None


def test_damage_detail_action_taps_label_center_without_purchase_offset():
    screen = _load(ATTACK_FIXTURE)

    with patch("core.input._dispatch_tap") as dispatch:
        assert tap_if_visible("buttons.damage_adjuster:attack", screenshot=screen)

    dispatch.assert_called_once_with(
        158,
        1354,
        label="buttons.damage_adjuster:attack",
        dispatch="now",
    )


def test_open_damage_adjuster_uses_settled_screenshot_workflow():
    frames = iter((_load(ATTACK_FIXTURE), _load(PANEL_FIXTURE)))
    taps = []

    def tap_visible(name, **kwargs):
        taps.append((name, kwargs))
        return True

    reading = open_damage_adjuster(
        capture_fn=lambda: next(frames),
        tap_visible_fn=tap_visible,
        sleep_fn=lambda _seconds: None,
    )

    assert reading is not None
    assert reading.percentage == "1E-22%"
    assert taps[0][0] == "buttons.damage_adjuster:attack"
    assert taps[0][1]["retries"] == 1


def test_dismiss_is_guarded_and_restores_attack_menu():
    frames = iter((_load(PANEL_FIXTURE), _load(ATTACK_FIXTURE)))
    taps = []

    def tap(name, **kwargs):
        taps.append((name, kwargs))
        return True

    assert dismiss_damage_adjuster(
        capture_fn=lambda: next(frames),
        tap_fn=tap,
        sleep_fn=lambda _seconds: None,
    )
    assert taps == [
        (
            "gesture_targets.dismiss_damage_adjuster",
            {"require_visible": False, "dispatch": "now"},
        )
    ]
    assert get_click("gesture_targets.dismiss_damage_adjuster") == (50, 50)
