from unittest.mock import patch

from core.clickmap_access import get_click
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
    with (
        patch("core.target_priority.read_target_priority_order", side_effect=reads),
        patch("core.target_priority.log"),
    ):
        assert ensure_target_priority_order(
            tap_fn=lambda point: taps.append(point) or True,
            ensure_menu_fn=lambda: True,
            sleep_fn=lambda _seconds: None,
        )
    assert taps[0] == (910, 380)
    assert taps[-1] == (950, 100)


def test_home_boundary_can_supply_an_already_open_priority_panel():
    taps = []
    with (
        patch(
            "core.target_priority.read_target_priority_order",
            side_effect=(list(TARGETS), list(TARGETS)),
        ),
        patch("core.target_priority.log"),
    ):
        assert ensure_target_priority_order(
            tap_fn=lambda point: taps.append(point) or True,
            ensure_menu_fn=lambda: (_ for _ in ()).throw(
                AssertionError("in-run menu must not open from Home")
            ),
            sleep_fn=lambda _seconds: None,
            panel_open=True,
        )

    assert get_click("navigation.home_target_priority") == (1025, 620)
    assert taps == [(950, 100)]


def test_priority_comparison_is_case_insensitive_and_ordered():
    assert target_priority_matches(TARGETS, [item.lower() for item in TARGETS])
    assert not target_priority_matches(TARGETS, tuple(reversed(TARGETS)))


def test_observer_reads_and_closes_without_reordering():
    actual = list(reversed(TARGETS))
    taps = []
    with (
        patch("core.target_priority.read_target_priority_order", return_value=actual),
        patch("core.target_priority.log"),
    ):
        observation = observe_target_priority_order(
            tap_fn=lambda point: taps.append(point) or True,
            ensure_menu_fn=lambda: True,
            sleep_fn=lambda _seconds: None,
        )

    assert observation.observed
    assert observation.matches is False
    assert observation.actual == tuple(actual)
    assert taps == [(910, 380), (950, 100)]
