from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2

from automation.missions.base import MissionContext
from core.action_executor import execute_actions
from core.label_tapper import get_label_match
from core.orb_distance import (
    OrbDistanceReading,
    RangeReading,
    configure_orb_distance,
    dismiss_orb_distance,
    normalize_distance,
    normalize_orb_distance_preset,
    open_orb_distance,
    read_attack_range,
    read_orb_distance,
)
from core.state_detector import detect_state_and_overlays


ROOT = Path(__file__).resolve().parents[1]
PANEL_FIXTURE = (
    ROOT / "test" / "fixtures" / "ui_state_20260714"
    / "active_distance_adjuster.png"
)
FARM_RANGE_FIXTURE = (
    ROOT / "test" / "fixtures" / "attack_menu_damage_max_20260714.png"
)
TOURNAMENT_RANGE_FIXTURE = (
    ROOT / "test" / "fixtures"
    / "running_menu_no_reward_badges_20260715.png"
)


def _load(path: Path):
    image = cv2.imread(str(path))
    assert image is not None, f"fixture is unreadable: {path}"
    return image


def _reading(extra, workshop, *, confidence=92.0, screenshot=None):
    if screenshot is None:
        screenshot = _load(PANEL_FIXTURE)
    return OrbDistanceReading(
        visible=True,
        extra=extra,
        workshop=workshop,
        extra_ocr_text=str(extra or ""),
        workshop_ocr_text=str(workshop or ""),
        extra_ocr_confidence=confidence,
        workshop_ocr_confidence=confidence,
        panel_confidence=1.0,
        screenshot=screenshot,
    )


def _range(distance):
    return RangeReading(
        distance=distance,
        ocr_text=str(distance or ""),
        ocr_confidence=93.0,
    )


def test_distance_adjuster_fixture_reads_both_orb_values():
    screen = _load(PANEL_FIXTURE)

    detection = detect_state_and_overlays(screen)
    reading = read_orb_distance(screen)

    assert detection["state"] == "DISTANCE_ADJUSTER"
    assert reading.visible
    assert reading.authoritative
    assert reading.extra == "30.00m"
    assert reading.workshop == "39.00m"
    assert reading.panel_confidence >= 0.99
    assert reading.extra_ocr_confidence >= 90
    assert reading.workshop_ocr_confidence >= 90


def test_incomplete_frame_cannot_authorize_orb_distance_values():
    panel = _load(PANEL_FIXTURE)
    incomplete = panel * 0
    incomplete[975:1775, 70:1010] = panel[975:1775, 70:1010]

    reading = read_orb_distance(incomplete)

    assert not reading.visible
    assert not reading.authoritative


def test_distance_normalization_and_preset_validation():
    assert normalize_distance("30") == "30.00m"
    assert normalize_distance("87.16m") == "87.16m"
    assert normalize_orb_distance_preset(
        {
            "range_basis": "98.38",
            "extra": "87.16m",
            "workshop": "80.37",
        }
    ) == {
        "range_basis": "98.38m",
        "extra": "87.16m",
        "workshop": "80.37m",
    }


def test_range_guard_reads_farm_and_tournament_fixtures():
    for fixture, expected in (
        (FARM_RANGE_FIXTURE, "30.00m"),
        (TOURNAMENT_RANGE_FIXTURE, "98.38m"),
    ):
        screen = _load(fixture)
        result = SimpleNamespace(
            screenshot=screen,
            box=SimpleNamespace(rect=(26, 1680, 511, 190)),
        )
        reading = read_attack_range(
            capture_fn=lambda: screen,
            find_upgrade_fn=lambda *_args, result=result, **_kwargs: result,
            sleep_fn=lambda _seconds: None,
        )

        assert reading.authoritative
        assert reading.distance == expected


def test_orb_distance_runtime_targets_match_retained_evidence():
    panel = _load(PANEL_FIXTURE)
    menu = _load(TOURNAMENT_RANGE_FIXTURE)
    cases = (
        ("navigation.distance_adjuster", menu),
        ("buttons.close:distance_adjuster", panel),
        ("buttons.distance_adjuster:extra:decrease", panel),
        ("buttons.distance_adjuster:extra:increase", panel),
        ("buttons.distance_adjuster:workshop:decrease", panel),
        ("buttons.distance_adjuster:workshop:increase", panel),
    )

    for target, screenshot in cases:
        match = get_label_match(
            target,
            screenshot=screenshot,
            return_meta=True,
        )
        assert match["match_score"] >= 0.9


def test_open_orb_distance_uses_verified_side_menu_control():
    menu = _load(TOURNAMENT_RANGE_FIXTURE)
    panel = _load(PANEL_FIXTURE)
    frames = iter((menu, menu, panel))
    taps = []

    reading = open_orb_distance(
        capture_fn=lambda: next(frames),
        tap_visible_fn=lambda name, **kwargs: taps.append(
            (name, kwargs)
        ) or True,
        ensure_menu_fn=lambda: True,
        sleep_fn=lambda _seconds: None,
    )

    assert reading is not None and reading.authoritative
    assert taps[0][0] == "navigation.distance_adjuster"
    assert taps[0][1]["screenshot"] is menu


def test_dismiss_orb_distance_uses_verified_close_control():
    panel = _load(PANEL_FIXTURE)
    menu = _load(TOURNAMENT_RANGE_FIXTURE)
    frames = iter((panel, menu))
    taps = []

    assert dismiss_orb_distance(
        capture_fn=lambda: next(frames),
        tap_visible_fn=lambda name, **kwargs: taps.append(
            (name, kwargs)
        ) or True,
        sleep_fn=lambda _seconds: None,
    )
    assert taps[0][0] == "buttons.close:distance_adjuster"
    assert taps[0][1]["screenshot"] is panel


def test_orb_distance_already_at_target_sends_no_arrow_tap():
    taps = []
    with (
        patch(
            "core.orb_distance.open_orb_distance",
            return_value=_reading("30.00m", "39.00m"),
        ),
        patch("core.orb_distance.dismiss_orb_distance", return_value=True),
    ):
        result = configure_orb_distance(
            range_basis="30m",
            extra="30m",
            workshop="39m",
            read_range_fn=lambda **_kwargs: _range("30.00m"),
            tap_visible_fn=lambda *args, **kwargs: taps.append(
                (args, kwargs)
            ) or True,
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert not result.changed
    assert result.extra_steps == 0
    assert result.workshop_steps == 0
    assert taps == []


def test_orb_distance_enforces_each_row_with_single_step_feedback():
    reads = iter(
        (
            _reading("31.00m", "39.00m"),
            _reading("31.00m", "40.00m"),
        )
    )
    taps = []
    with (
        patch(
            "core.orb_distance.open_orb_distance",
            return_value=_reading("30.00m", "39.00m"),
        ),
        patch(
            "core.orb_distance.read_orb_distance",
            side_effect=lambda _frame: next(reads),
        ),
        patch("core.orb_distance.dismiss_orb_distance", return_value=True),
    ):
        result = configure_orb_distance(
            range_basis="30m",
            extra="31m",
            workshop="40m",
            capture_fn=lambda: object(),
            read_range_fn=lambda **_kwargs: _range("30.00m"),
            tap_visible_fn=lambda name, **_kwargs: taps.append(name) or True,
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.changed
    assert result.final_extra == "31.00m"
    assert result.final_workshop == "40.00m"
    assert result.extra_steps == 1
    assert result.workshop_steps == 1
    assert taps == [
        "buttons.distance_adjuster:extra:increase",
        "buttons.distance_adjuster:workshop:increase",
    ]


def test_range_basis_mismatch_blocks_before_opening_or_tapping():
    with patch("core.orb_distance.open_orb_distance") as open_panel:
        result = configure_orb_distance(
            range_basis="30m",
            extra="30m",
            workshop="39m",
            read_range_fn=lambda **_kwargs: _range("98.38m"),
            tap_visible_fn=lambda *_args, **_kwargs: True,
            sleep_fn=lambda _seconds: None,
        )

    assert not result.success
    assert result.range_observed == "98.38m"
    assert result.reason == "range_basis_mismatch"
    open_panel.assert_not_called()


def test_action_executor_records_successful_orb_distance_enforcement():
    payload = {
        "range_basis": "30.00m",
        "final_extra": "30.00m",
        "final_workshop": "39.00m",
        "success": True,
    }
    result = SimpleNamespace(
        success=True,
        range_basis="30.00m",
        range_observed="30.00m",
        expected_extra="30.00m",
        expected_workshop="39.00m",
        initial_extra="87.16m",
        initial_workshop="80.37m",
        final_extra="30.00m",
        final_workshop="39.00m",
        extra_steps=4,
        workshop_steps=3,
        reason="matched",
        as_dict=lambda: payload,
    )
    ctx = MissionContext(
        data={"mission_vars": {"last_detection_state": "RUNNING"}}
    )
    action = {
        "type": "orb_distance_configure",
        "mode": "enforce",
        "range_basis": "30.00m",
        "extra": "30.00m",
        "workshop": "39.00m",
        "_strategy": True,
    }

    with (
        patch(
            "core.action_executor.configure_orb_distance",
            return_value=result,
        ) as configure,
        patch("core.action_executor.log_mission") as mission_log,
    ):
        execute_actions(object(), [action], ctx)

    configure.assert_called_once_with(
        range_basis="30.00m",
        extra="30.00m",
        workshop="39.00m",
        mode="enforce",
    )
    assert ctx.data["mission_vars"]["orb_distance_checked"]
    assert ctx.data["mission_vars"]["orb_distance_observation"] == payload
    mission_log.assert_called_once_with(
        "[ORB_DISTANCE] mode=enforce range=30.00m/30.00m "
        "expected=(30.00m,39.00m) initial=(87.16m,80.37m) "
        "final=(30.00m,39.00m) steps=(4,3) success=True reason=matched",
        "INFO",
    )
