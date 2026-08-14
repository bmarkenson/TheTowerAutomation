from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2

from core.clickmap_access import get_click
from core.damage_adjuster import (
    DAMAGE_SELECTOR_MODE,
    DamageAdjusterReading,
    configure_damage_slider,
    dismiss_damage_adjuster,
    format_damage_percentage,
    normalize_damage_percentage,
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


def test_incomplete_black_frame_cannot_authorize_damage_panel_evidence():
    panel = _load(PANEL_FIXTURE)
    incomplete = panel * 0
    incomplete[1575:1850, 60:1020] = panel[1575:1850, 60:1020]

    reading = read_damage_adjuster(incomplete)

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


def test_damage_slider_arrow_actions_have_explicit_geometry():
    assert get_click("buttons.damage_adjuster:decrease") == (195, 1755)
    assert get_click("buttons.damage_adjuster:increase") == (885, 1755)


def test_damage_percentage_normalization_uses_screen_notation():
    assert normalize_damage_percentage("1e-22") == "1E-22%"
    assert normalize_damage_percentage("1E-22%") == "1E-22%"
    assert normalize_damage_percentage("100%") == "1E2%"


def test_damage_percentage_operator_format_uses_screen_notation():
    assert format_damage_percentage("1E2%") == "100%"
    assert format_damage_percentage("1E1%") == "10%"
    assert format_damage_percentage("1E-22%") == "1E-22%"
    assert format_damage_percentage(None) is None


def _reading(percentage, *, confidence=95.0, screenshot=None):
    if screenshot is None:
        screenshot = _load(PANEL_FIXTURE)
    return DamageAdjusterReading(
        visible=True,
        mode=DAMAGE_SELECTOR_MODE,
        percentage=percentage,
        ocr_text=f"Percent Of Enemy Health {percentage}",
        ocr_confidence=confidence,
        panel_confidence=1.0,
        screenshot=screenshot,
    )


def test_damage_slider_enforcement_batches_known_power_of_ten_steps():
    taps = []

    def tap(name, **kwargs):
        taps.append((name, kwargs))
        return True

    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-20%")),
        patch(
            "core.damage_adjuster.read_damage_adjuster",
            return_value=_reading("1E-22%"),
        ) as read,
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1e-22",
            capture_fn=lambda: object(),
            tap_fn=tap,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.initial == "1E-20%"
    assert result.final == "1E-22%"
    assert result.changed
    assert result.steps == 2
    assert [point for point, _kwargs in taps] == [(195, 1755)] * 2
    verifications = [kwargs["verification"] for _point, kwargs in taps]
    assert verifications[0] is verifications[1]
    assert verifications[0].reuse_authority
    assert all(
        kwargs["dispatch"] == "now"
        and kwargs["log_label"] == "buttons.damage_adjuster:decrease"
        for _point, kwargs in taps
    )
    assert read.call_count == 1


def test_damage_slider_operator_logs_format_integral_target():
    with (
        patch(
            "core.damage_adjuster.open_damage_adjuster",
            return_value=_reading("1E-22%"),
        ),
        patch(
            "core.damage_adjuster.read_damage_adjuster",
            return_value=_reading("100%"),
        ),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
        patch("core.damage_adjuster.log_action_intent") as action_log,
        patch("core.damage_adjuster.log") as progress_log,
    ):
        result = configure_damage_slider(
            "100%",
            capture_fn=lambda: object(),
            tap_fn=lambda *_args, **_kwargs: True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.expected == "1E2%"
    assert action_log.call_args.args[0] == "Setting the Damage Slider to 100%"
    assert any(
        "Applying 24 increase tap(s) from 1E-22% toward 100%"
        in call.args[0]
        for call in progress_log.call_args_list
    )


def test_damage_slider_batches_full_live_observed_exponent_gap():
    taps = []
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("100%")),
        patch(
            "core.damage_adjuster.read_damage_adjuster",
            return_value=_reading("1E-22%"),
        ) as read,
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-22%",
            capture_fn=lambda: object(),
            tap_fn=lambda name, **_kwargs: taps.append(name) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.steps == 24
    assert taps == [(195, 1755)] * 24
    assert read.call_count == 1


def test_damage_slider_recomputes_batch_after_dropped_steps_settle():
    reads = iter(
        (
            _reading("1E-21%"),
            _reading("1E-21%"),
            _reading("1E-22%"),
        )
    )
    taps = []
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-20%")),
        patch("core.damage_adjuster.read_damage_adjuster", side_effect=lambda _frame: next(reads)),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-22%",
            capture_fn=lambda: object(),
            tap_fn=lambda name, **_kwargs: taps.append(name) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.steps == 3
    assert taps == [(195, 1755)] * 3


def test_damage_slider_unknown_sequence_keeps_single_step_feedback():
    taps = []
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("5%")),
        patch(
            "core.damage_adjuster.read_damage_adjuster",
            return_value=_reading("1%"),
        ) as read,
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1%",
            capture_fn=lambda: object(),
            tap_fn=lambda name, **_kwargs: taps.append(name) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert result.steps == 1
    assert taps == [(195, 1755)]
    assert read.call_count == 1


def test_damage_slider_stops_after_a_partial_batch_dispatch_failure():
    reads = iter((_reading("1E-21%"), _reading("1E-21%")))
    tap_results = iter((True, False))
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-20%")),
        patch("core.damage_adjuster.read_damage_adjuster", side_effect=lambda _frame: next(reads)),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-22%",
            capture_fn=lambda: object(),
            tap_fn=lambda *_args, **_kwargs: next(tap_results),
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert not result.success
    assert result.steps == 1
    assert result.final == "1E-21%"
    assert result.reason == "arrow_tap_failed"


def test_damage_slider_already_at_target_sends_no_arrow_tap():
    taps = []
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-22%")),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-22%",
            capture_fn=lambda: object(),
            tap_fn=lambda *args, **kwargs: taps.append((args, kwargs)) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert not result.changed
    assert result.steps == 0
    assert taps == []


def test_damage_slider_observes_initial_value_and_invalidates_before_repair():
    events = []
    with (
        patch(
            "core.damage_adjuster.open_damage_adjuster",
            return_value=_reading("1E-20%"),
        ),
        patch(
            "core.damage_adjuster.read_damage_adjuster",
            return_value=_reading("1E-21%"),
        ),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-21%",
            capture_fn=lambda: object(),
            tap_fn=lambda *_args, **_kwargs: events.append("tap") or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            initial_evidence_observer_fn=lambda reading: events.append(
                f"observe:{reading.percentage}"
            ),
            repair_observer_fn=lambda: events.append("invalidate"),
            sleep_fn=lambda _seconds: None,
        )

    assert result.success
    assert events == ["observe:1E-20%", "invalidate", "tap"]


def test_damage_slider_invalidation_failure_blocks_repair_input():
    taps = []

    def fail_invalidation():
        raise RuntimeError("invalidation unavailable")

    with (
        patch(
            "core.damage_adjuster.open_damage_adjuster",
            return_value=_reading("1E-20%"),
        ),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-21%",
            capture_fn=lambda: object(),
            tap_fn=lambda *args, **kwargs: taps.append((args, kwargs)) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            repair_observer_fn=fail_invalidation,
            sleep_fn=lambda _seconds: None,
        )

    assert not result.success
    assert result.reason == "snapshot_invalidation_failed"
    assert result.steps == 0
    assert taps == []


def test_damage_slider_observation_reports_mismatch_without_changing_value():
    taps = []
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-20%")),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
        patch("core.damage_adjuster.log_result") as result_log,
    ):
        result = configure_damage_slider(
            "1E-22%",
            mode="observe",
            capture_fn=lambda: object(),
            tap_fn=lambda *args, **kwargs: taps.append((args, kwargs)) or True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.observed
    assert not result.matches
    assert not result.success
    assert result.reason == "observed_mismatch"
    assert taps == []
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Damage Slider check complete — observed 1E-20%, expected 1E-22%"
    )


def test_damage_slider_enforcement_fails_closed_when_feedback_moves_away():
    reads = iter((_reading("1E-19%"), _reading("1E-19%")))
    with (
        patch("core.damage_adjuster.open_damage_adjuster", return_value=_reading("1E-20%")),
        patch("core.damage_adjuster.read_damage_adjuster", side_effect=lambda _frame: next(reads)),
        patch("core.damage_adjuster.dismiss_damage_adjuster", return_value=True),
    ):
        result = configure_damage_slider(
            "1E-22%",
            capture_fn=lambda: object(),
            tap_fn=lambda *_args, **_kwargs: True,
            ensure_menu_fn=lambda *_args, **_kwargs: object(),
            sleep_fn=lambda _seconds: None,
        )

    assert not result.success
    assert result.steps == 2
    assert result.reason == "value_moved_away_from_target"


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
    assert taps[0][1]["failure_log_level"] == "DEBUG"


def test_open_damage_adjuster_finds_damage_above_current_viewport():
    attack = _load(ATTACK_FIXTURE)
    panel = _load(PANEL_FIXTURE)
    scrolled = attack.copy()
    scrolled[1253:1477, 26:276] = 0
    current = scrolled
    swipes = []
    tap_frames = []

    def capture():
        return current

    def swipe(direction, span):
        nonlocal current
        swipes.append((direction, span))
        current = attack

    def tap_visible(name, **kwargs):
        nonlocal current
        tap_frames.append((name, kwargs["screenshot"]))
        if kwargs["screenshot"] is scrolled:
            return False
        current = panel
        return True

    reading = open_damage_adjuster(
        capture_fn=capture,
        tap_visible_fn=tap_visible,
        swipe_fn=swipe,
        sleep_fn=lambda _seconds: None,
    )

    assert reading is not None
    assert reading.percentage == "1E-22%"
    assert swipes == [("towards_top", "short")]
    assert [name for name, _frame in tap_frames] == [
        "buttons.damage_adjuster:attack",
        "buttons.damage_adjuster:attack",
    ]
    assert tap_frames[0][1] is scrolled
    assert tap_frames[1][1] is attack


def test_open_damage_adjuster_fails_closed_if_attack_changes_during_search():
    attack = _load(ATTACK_FIXTURE)
    changed = _load(PANEL_FIXTURE)
    frames = iter((attack, changed, changed))
    finder = SimpleNamespace(called=False)

    def find_upgrade_stub(_menu, _label, **kwargs):
        finder.called = True
        assert kwargs["capture_fn"]() is None
        return None

    reading = open_damage_adjuster(
        capture_fn=lambda: next(frames),
        tap_visible_fn=lambda *_args, **_kwargs: False,
        find_upgrade_fn=find_upgrade_stub,
        sleep_fn=lambda _seconds: None,
    )

    assert reading is None
    assert finder.called


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
    assert len(taps) == 1
    target, kwargs = taps[0]
    assert target == "gesture_targets.dismiss_damage_adjuster"
    assert kwargs["dispatch"] == "now"
    assert kwargs["verification"].description == "damage_adjuster:visible_backdrop"
    assert get_click("gesture_targets.dismiss_damage_adjuster") == (50, 50)
