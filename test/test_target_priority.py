from unittest.mock import patch

import numpy as np

from core.clickmap_access import resolve_dot_path
from core.target_priority import (
    TARGETS,
    _canonical_target,
    ensure_target_priority_order,
    observe_target_priority_order,
    target_priority_matches,
)


def test_ocr_noise_is_canonicalised():
    assert _canonical_target("| FLEETS") == "Fleets"
    assert _canonical_target("__ IN SPOTLIGHT") == "In Spotlight"
    assert _canonical_target("OSEST (DEFAULT)") == "Closest (Default)"


def test_enforcer_moves_rows_up_and_verifies():
    reads = iter((list(reversed(TARGETS)), list(TARGETS)))
    taps = []
    repairs = []
    with (
        patch("core.target_priority.read_target_priority_order", side_effect=reads),
        patch("core.target_priority.log"),
        patch("core.target_priority.log_result") as result_log,
    ):
        assert ensure_target_priority_order(
            capture_fn=lambda: np.full((1920, 1080, 3), 32, dtype=np.uint8),
            tap_fn=lambda point, **_kwargs: taps.append(point) or True,
            ensure_menu_fn=lambda: True,
            sleep_fn=lambda _seconds: None,
            repair_observer_fn=lambda: repairs.append("started"),
        )
    assert taps[0] == (910, 380)
    assert taps[-1] == (950, 100)
    assert repairs == ["started"]
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Target Priority setup complete — order verified"
    )


def test_running_boundary_can_supply_an_already_open_priority_panel():
    taps = []
    repairs = []
    with (
        patch(
            "core.target_priority.read_target_priority_order",
            side_effect=(list(TARGETS), list(TARGETS)),
        ),
        patch("core.target_priority.log"),
    ):
        assert ensure_target_priority_order(
            tap_fn=lambda point, **_kwargs: taps.append(point) or True,
            ensure_menu_fn=lambda: (_ for _ in ()).throw(
                AssertionError("menu is already open")
            ),
            sleep_fn=lambda _seconds: None,
            panel_open=True,
            repair_observer_fn=lambda: repairs.append("started"),
        )

    assert resolve_dot_path("navigation.home_target_priority") is None
    assert taps == [(950, 100)]
    assert repairs == []


def test_priority_comparison_is_case_insensitive_and_ordered():
    assert target_priority_matches(TARGETS, [item.lower() for item in TARGETS])
    assert not target_priority_matches(TARGETS, tuple(reversed(TARGETS)))


def test_observer_reads_and_closes_without_reordering():
    actual = list(reversed(TARGETS))
    taps = []
    with (
        patch("core.target_priority.read_target_priority_order", return_value=actual),
        patch("core.target_priority.log"),
        patch("core.target_priority.log_result") as result_log,
    ):
        observation = observe_target_priority_order(
            tap_fn=lambda point, **_kwargs: taps.append(point) or True,
            ensure_menu_fn=lambda: True,
            sleep_fn=lambda _seconds: None,
        )

    assert observation.observed
    assert observation.matches is False
    assert observation.actual == tuple(actual)
    assert taps == [(910, 380), (950, 100)]
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Target Priority check complete — order differs from the strategy"
    )
