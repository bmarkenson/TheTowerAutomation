from unittest.mock import patch

import numpy as np

from core.clickmap_access import get_click, get_explicit_tap, resolve_dot_path
from core.input import (
    TapVerification,
    safe_long_press,
    safe_tap,
    swipe_now,
    tap_if_visible,
    tap_unchecked_for_tooling,
)
from core.label_tapper import swipe_relative_in_region


def test_legacy_get_click_preserves_direct_match_region_center():
    entry = resolve_dot_path("buttons.battle:home")
    assert entry is not None
    assert "match_region" in entry
    assert "tap" not in entry

    assert get_click("buttons.battle:home") == (538, 1559)
    assert get_explicit_tap("buttons.battle:home") is None


def test_broad_region_ref_never_becomes_a_click_center():
    entry = resolve_dot_path("upgrades.utility.left.EHLS")
    assert entry is not None
    assert entry["region_ref"] == "upgrades_left"

    assert get_click("upgrades.utility.left.EHLS") is None


def test_runtime_static_tap_requires_target_verification():
    with patch("core.input._dispatch_tap") as dispatch:
        assert not safe_tap("buttons.battle_control:home")

    dispatch.assert_not_called()


def test_safe_tap_records_input_summary_and_coordinate_detail(
    tmp_path,
    monkeypatch,
):
    action_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(action_log))
    screenshot = np.full((1920, 1080, 3), 32, dtype=np.uint8)

    with patch("core.input._dispatch_tap") as dispatch:
        assert safe_tap(
            (10, 20),
            dispatch="queue",
            log_label="test_target",
            verification=TapVerification(
                screenshot=screenshot,
                target_region=(0, 0, 30, 40),
                description="unit_test_target",
                verifier=lambda _frame: True,
            ),
        )

    dispatch.assert_called_once_with(
        10,
        20,
        label="test_target",
        dispatch="queue",
    )
    lines = action_log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("[INPUT ")
    assert lines[0].endswith("] Tap queued: Test target")
    assert lines[1].endswith(
        "] TAP_SAFE now=False label=test_target at (10,20) "
        "verified=unit_test_target"
    )


def test_coordinate_tap_fails_closed_when_verifier_rejects_target():
    screenshot = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    verification = TapVerification(
        screenshot=screenshot,
        target_region=(0, 0, 30, 40),
        description="rejected_target",
        verifier=lambda _frame: False,
    )

    with patch("core.input._dispatch_tap") as dispatch:
        assert not safe_tap((10, 20), verification=verification)

    dispatch.assert_not_called()


def test_template_probe_can_keep_handled_failure_diagnostic():
    with (
        patch(
            "core.input.get_label_match",
            side_effect=ValueError("match below threshold"),
        ),
        patch("core.input.log") as log,
        patch("core.input._dispatch_tap") as dispatch,
    ):
        assert not tap_if_visible(
            "buttons.damage_adjuster:attack",
            failure_log_level="DEBUG",
        )

    dispatch.assert_not_called()
    log.assert_called_once_with(
        "[SKIP] TAP_SAFE failed for buttons.damage_adjuster:attack: "
        "match below threshold",
        "DEBUG",
    )


def test_template_tap_failure_remains_warning_by_default():
    with (
        patch(
            "core.input.get_label_match",
            side_effect=ValueError("match below threshold"),
        ),
        patch("core.input.log") as log,
        patch("core.input._dispatch_tap") as dispatch,
    ):
        assert not tap_if_visible("buttons.damage_adjuster:attack")

    dispatch.assert_not_called()
    log.assert_called_once_with(
        "[SKIP] TAP_SAFE failed for buttons.damage_adjuster:attack: "
        "match below threshold",
        "WARN",
    )


def test_reusable_tap_authority_evaluates_initial_frame_once():
    screenshot = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    verifier_calls = 0

    def verifier(_frame):
        nonlocal verifier_calls
        verifier_calls += 1
        return True

    verification = TapVerification(
        screenshot=screenshot,
        target_region=(0, 0, 30, 40),
        description="urgent_batch",
        verifier=verifier,
        reuse_authority=True,
    )

    with patch("core.input._dispatch_tap") as dispatch:
        assert safe_tap((10, 20), verification=verification)
        assert safe_tap((10, 20), verification=verification)

    assert dispatch.call_count == 2
    assert verifier_calls == 1


def test_safe_long_press_uses_fresh_template_geometry_and_configured_offset():
    screenshot = np.full((1920, 1080, 3), 32, dtype=np.uint8)

    with (
        patch(
            "core.input.get_label_match",
            return_value=(300, 1200, 230, 43),
        ) as match,
        patch("core.input._dispatch_swipe") as dispatch,
        patch("core.input.log_input") as input_log,
    ):
        assert safe_long_press(
            "buttons.card_inventory:demon_mode",
            duration_ms=800,
            screenshot=screenshot,
        )

    match.assert_called_once_with(
        "buttons.card_inventory:demon_mode",
        screenshot=screenshot,
    )
    dispatch.assert_called_once_with(
        415,
        1280,
        415,
        1280,
        800,
    )
    input_log.assert_called_once()
    assert input_log.call_args.args == (
        "Long press requested: Card inventory (demon mode)",
    )
    assert "duration_ms=800" in input_log.call_args.kwargs["detail"]


def test_named_swipe_records_input_before_dispatch():
    events = []
    swipe = {
        "x1": 900,
        "y1": 390,
        "x2": 650,
        "y2": 390,
        "duration_ms": 250,
    }

    with (
        patch("core.input.get_swipe", return_value=swipe),
        patch(
            "core.input.log_input",
            side_effect=lambda *args, **kwargs: events.append(
                ("input", args, kwargs)
            ),
        ),
        patch(
            "core.input._dispatch_swipe",
            side_effect=lambda *args, **kwargs: events.append(
                ("swipe", args, kwargs)
            ),
        ),
    ):
        assert swipe_now("gesture_targets.goto_next:weekly_mission_chests")

    assert [kind for kind, _args, _kwargs in events] == ["input", "swipe"]
    assert events[0][1] == ("Swipe requested: Go to next (weekly mission chests)",)
    assert "SWIPE_NOW" in events[0][2]["detail"]


def test_unchecked_tooling_tap_records_input_before_dispatch():
    events = []

    with (
        patch("core.input.get_click", return_value=(136, 1864)),
        patch(
            "core.input.log_input",
            side_effect=lambda *args, **kwargs: events.append(
                ("input", args, kwargs)
            ),
        ),
        patch(
            "core.input._dispatch_tap",
            side_effect=lambda *args, **kwargs: events.append(
                ("tap", args, kwargs)
            ),
        ),
    ):
        assert tap_unchecked_for_tooling(
            "navigation.goto_attack",
            reason="explicit test",
        )

    assert [kind for kind, _args, _kwargs in events] == ["input", "tap"]
    assert events[0][1] == ("Unchecked tooling tap requested: Go to attack",)
    assert "reason=explicit test" in events[0][2]["detail"]


def test_relative_swipe_records_input_before_dispatch():
    events = []

    with (
        patch(
            "core.label_tapper.log_input",
            side_effect=lambda *args, **kwargs: events.append(
                ("input", args, kwargs)
            ),
        ),
        patch(
            "core.label_tapper.input_swipe",
            side_effect=lambda *args, **kwargs: events.append(
                ("swipe", args, kwargs)
            ),
        ),
    ):
        swipe_relative_in_region((0, 0, 1000, 1000))

    assert [kind for kind, _args, _kwargs in events] == ["input", "swipe"]
    assert events[0][1] == ("Swipe requested within detected region",)
    assert "SWIPE_REL" in events[0][2]["detail"]


def test_safe_long_press_rejects_static_targets():
    with patch("core.input._dispatch_swipe") as dispatch:
        assert not safe_long_press("gesture_targets.dismiss_damage_adjuster")

    dispatch.assert_not_called()


def test_navigation_targets_preserve_expected_tap_geometry():
    assert get_click("navigation.goto_attack") == (136, 1864)
    assert get_click("navigation.goto_defense") == (406, 1868)
    assert get_click("navigation.goto_utility") == (670, 1867)
    assert get_click("navigation.goto_uw") == (941, 1871)
    assert get_click("navigation.workshop:uw") == (941, 1686)
    assert get_click("navigation.goto_home_store") == (80, 1830)
    assert get_click("navigation.goto_modules_home") == (630, 1830)


def test_module_loadout_inspection_targets_have_explicit_tap_geometry():
    assert get_click("buttons.module:equipped_outer_left_top") == (115, 407)
    assert get_click("buttons.module:equipped_inner_left_top") == (307, 407)
    assert get_click("buttons.module:equipped_inner_right_top") == (773, 407)
    assert get_click("buttons.module:equipped_outer_right_top") == (964, 407)
    assert get_click("buttons.module:equipped_outer_left_bottom") == (115, 663)
    assert get_click("buttons.module:equipped_inner_left_bottom") == (307, 663)
    assert get_click("buttons.module:equipped_inner_right_bottom") == (773, 663)
    assert get_click("buttons.module:equipped_outer_right_bottom") == (964, 663)
    assert get_click("buttons.close:module_detail") == (929, 223)
