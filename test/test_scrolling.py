from unittest.mock import patch

import numpy as np

from core.scrolling import (
    capture_scroll_to_edge,
    guarded_swipe,
    scroll_to_edge,
    scroll_until_visible,
)


def _frame(value: int) -> np.ndarray:
    return np.full((12, 12, 3), value, dtype=np.uint8)


def test_guarded_swipe_refuses_wrong_source_screen():
    swipes = []
    result = guarded_swipe(
        "gesture.test",
        source_label="indicators.expected",
        screenshot=_frame(1),
        visible_fn=lambda _label, **_kwargs: False,
        swipe_fn=lambda key: swipes.append(key) or True,
        sleep_fn=lambda _seconds: None,
    )

    assert not result.success
    assert result.reason == "wrong_source_screen"
    assert result.swipes == 0
    assert swipes == []


def test_scroll_to_edge_stops_when_settled_content_is_stable():
    captures = iter((_frame(30), _frame(30)))
    swipes = []
    result = scroll_to_edge(
        "gesture.edge",
        source_label="indicators.expected",
        screenshot=_frame(10),
        max_swipes=5,
        stable_threshold=0.0,
        capture_fn=lambda: next(captures),
        visible_fn=lambda _label, **_kwargs: True,
        swipe_fn=lambda key: swipes.append(key) or True,
        sleep_fn=lambda _seconds: None,
    )

    assert result.success
    assert result.reason == "edge_reached"
    assert result.swipes == 2
    assert swipes == ["gesture.edge", "gesture.edge"]


def test_capture_scroll_to_edge_retains_each_distinct_viewport():
    captures = iter((_frame(20), _frame(30), _frame(30)))
    result = capture_scroll_to_edge(
        "gesture.edge",
        source_label="indicators.expected",
        screenshot=_frame(10),
        max_swipes=5,
        stable_threshold=0.0,
        capture_fn=lambda: next(captures),
        visible_fn=lambda _label, **_kwargs: True,
        swipe_fn=lambda _key: True,
        sleep_fn=lambda _seconds: None,
    )

    assert result.success
    assert result.reason == "edge_reached"
    assert result.swipes == 3
    assert [int(frame[0, 0, 0]) for frame in result.screenshots] == [10, 20, 30]


def test_capture_scroll_to_edge_stops_at_caller_proven_boundary():
    captures = iter((_frame(20), _frame(30), _frame(40)))
    result = capture_scroll_to_edge(
        "gesture.edge",
        source_label="indicators.expected",
        screenshot=_frame(10),
        max_swipes=5,
        stable_threshold=0.0,
        capture_fn=lambda: next(captures),
        visible_fn=lambda _label, **_kwargs: True,
        swipe_fn=lambda _key: True,
        sleep_fn=lambda _seconds: None,
        stop_fn=lambda frame: (
            "unchanged_timeline_row"
            if int(frame[0, 0, 0]) == 30
            else None
        ),
    )

    assert result.success
    assert result.reason == "unchanged_timeline_row"
    assert result.swipes == 2
    assert [int(frame[0, 0, 0]) for frame in result.screenshots] == [10, 20, 30]


def test_scroll_until_visible_returns_when_target_appears():
    captures = iter((_frame(2), _frame(3)))

    def visible(label, *, screenshot):
        if label == "indicators.expected":
            return True
        return label == "buttons.target" and int(screenshot[0, 0, 0]) == 3

    result = scroll_until_visible(
        "gesture.find",
        source_label="indicators.expected",
        target_label="buttons.target",
        screenshot=_frame(1),
        max_swipes=3,
        stable_threshold=0.0,
        capture_fn=lambda: next(captures),
        visible_fn=visible,
        swipe_fn=lambda _key: True,
        sleep_fn=lambda _seconds: None,
    )

    assert result.success
    assert result.reason == "target_visible"
    assert result.swipes == 2


def test_scroll_until_visible_honors_non_actionable_stop_condition():
    captures = iter((_frame(2),))
    result = scroll_until_visible(
        "gesture.find",
        source_label="indicators.expected",
        target_label="buttons.target",
        screenshot=_frame(1),
        max_swipes=3,
        capture_fn=lambda: next(captures),
        visible_fn=lambda label, **_kwargs: label == "indicators.expected",
        swipe_fn=lambda _key: True,
        sleep_fn=lambda _seconds: None,
        stop_fn=lambda frame: "not_ready" if int(frame[0, 0, 0]) == 2 else None,
    )

    assert not result.success
    assert result.reason == "not_ready"
    assert result.swipes == 1


def test_scroll_until_visible_records_edge_as_structured_debug_detail():
    with patch("core.scrolling.log") as log:
        result = scroll_until_visible(
            "gesture.find",
            source_label="indicators.expected",
            target_label="buttons.claim_weekly_mission_chest",
            screenshot=_frame(1),
            max_swipes=3,
            stable_threshold=0.0,
            capture_fn=lambda: _frame(1),
            visible_fn=lambda label, **_kwargs: (
                label == "indicators.expected"
            ),
            swipe_fn=lambda _key: True,
            sleep_fn=lambda _seconds: None,
        )

    assert not result.success
    assert result.reason == "edge_before_target"
    log.assert_called_once_with(
        "[SCROLL] Reached an edge before finding "
        "'buttons.claim_weekly_mission_chest'",
        "DEBUG",
    )
