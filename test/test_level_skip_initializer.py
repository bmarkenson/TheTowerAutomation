from threading import Event
from unittest.mock import Mock, patch

import numpy as np
import pytest

from automation.missions.base import MissionContext
from core.action_executor import execute_actions
from core.level_skip_initializer import (
    EALS,
    EALS_TAPS_PER_BURST,
    EHLS,
    LevelSkipInitializationResult,
    _default_scroll_to_bottom,
    _purchase_point,
    _tap_and_capture,
    initialize_level_skips,
)
from core.upgrade_box_detector import UpgradeBox
from utils.logger import log_input


def _frame(value: int):
    return np.full((1920, 1080, 3), value, dtype=np.uint8)


def _box(label: str, status: str) -> UpgradeBox:
    if label == EHLS:
        return UpgradeBox("left", (26, 1577, 511, 218), text=label, affordability=status)
    return UpgradeBox("right", (546, 1368, 509, 226), text=label, affordability=status)


def test_level_skip_purchase_points_are_centered_in_lower_buttons():
    assert _purchase_point(_box(EHLS, "affordable")) == (424, 1744)
    assert _purchase_point(_box(EALS, "affordable")) == (943, 1542)


def test_default_scroll_to_bottom_records_input_before_dispatch():
    events = []

    with (
        patch(
            "core.level_skip_initializer.resolve_dot_path",
            return_value={
                "match_region": {"x": 31, "y": 100, "w": 1000, "h": 1500}
            },
        ),
        patch(
            "core.level_skip_initializer.log_input",
            side_effect=lambda *args, **kwargs: events.append(
                ("input", args, kwargs)
            ),
        ),
        patch(
            "core.level_skip_initializer.input_swipe",
            side_effect=lambda *args, **kwargs: (
                events.append(("swipe", args, kwargs)),
                Mock(returncode=0),
            )[-1],
        ),
    ):
        assert _default_scroll_to_bottom()

    assert [kind for kind, _args, _kwargs in events] == ["input", "swipe"]
    assert events[0][1] == (
        "Swipe requested: Utility upgrade menu toward the bottom",
    )
    assert "RUN_INIT_SCROLL_TO_BOTTOM" in events[0][2]["detail"]
    assert events[1][1] == (531, 1330, 531, 430, 220)
    assert events[1][2] == {"check": False}


def test_fallback_initializer_taps_ehls_before_eals():
    initial = _frame(1)
    complete = _frame(3)
    captures = iter((complete,))
    taps = []
    events = []

    def boxes(frame):
        return {
            "left": [_box(EHLS, "affordable")],
            "right": [_box(EALS, "affordable")],
        }

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        maxed = value >= (2 if is_ehls else 3)
        if is_ehls and value == 3:
            events.append("ehls_max_observed")
        return maxed, {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            side_effect=lambda frame: {
                EHLS: boxes(frame)["left"][0],
                EALS: boxes(frame)["right"][0],
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            side_effect=lambda _frame: events.append("wave_ocr") or (20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: next(captures),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)),
                events.append(label),
                True,
            )[-1],
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
            mutation_observer_fn=lambda: events.append(
                "snapshot_invalidated"
            ),
        )

    assert result.success
    assert result.ehls_maxed and result.eals_maxed
    assert result.ehls_wave == 20
    assert result.eals_wave == 20
    assert result.eals_first_tap_wave == 20
    assert result.taps_sent == 2
    assert [label for label, _point in taps] == [
        f"level_skip:{EHLS}",
        f"level_skip:{EALS}",
    ]
    assert events.index(f"level_skip:{EHLS}") < events.index(
        f"level_skip:{EALS}"
    ) < events.index("ehls_max_observed")
    assert events.count("snapshot_invalidated") == 1
    assert events.index("snapshot_invalidated") < events.index(
        f"level_skip:{EHLS}"
    )
    first_wave_ocr = events.index("wave_ocr")
    assert all(
        index < first_wave_ocr
        for index, event in enumerate(events)
        if event.startswith("level_skip:")
    )


def test_initializer_observer_failure_prevents_purchase_mutation():
    initial = _frame(1)
    taps = []

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch(
            "core.level_skip_initializer.detect_current_buy_quantity",
            return_value="max",
        ),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "affordable"),
                EALS: _box(EALS, "affordable"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            return_value=(False, {}),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            mutation_observer_fn=lambda: (_ for _ in ()).throw(
                RuntimeError("synthetic invalidation failure")
            ),
        )

    assert result.success is False
    assert result.reason == "snapshot_invalidation_failed"
    assert result.taps_sent == 0
    assert taps == []


def test_initializer_invalidates_before_buy_quantity_mutation():
    initial = _frame(1)
    events = []

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch(
            "core.level_skip_initializer.detect_current_buy_quantity",
            return_value="x1",
        ),
        patch(
            "core.level_skip_initializer.ensure_buy_quantity",
            side_effect=lambda *args, **kwargs: (
                events.append("buy_quantity"),
                initial,
            )[-1],
        ),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "maxed"),
                EALS: _box(EALS, "maxed"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            return_value=(True, {}),
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            mutation_observer_fn=lambda: events.append("invalidate"),
        )

    assert result.success
    assert events == ["invalidate", "buy_quantity"]


def test_initializer_does_not_invalidate_when_both_level_skips_are_maxed():
    initial = _frame(1)
    observations = []

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch(
            "core.level_skip_initializer.detect_current_buy_quantity",
            return_value="max",
        ),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "maxed"),
                EALS: _box(EALS, "maxed"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            return_value=(True, {}),
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            mutation_observer_fn=lambda: observations.append("invalidate"),
        )

    assert result.success
    assert result.taps_sent == 0
    assert observations == []


def test_guarded_screenshot_path_is_the_production_default():
    initial = _frame(1)
    complete = _frame(3)
    captures = iter((complete,))
    taps = []

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch(
            "core.level_skip_initializer.detect_current_buy_quantity",
            return_value="max",
        ),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "affordable"),
                EALS: _box(EALS, "affordable"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(10, 99.0),
        ),
        patch(
            "core.level_skip_initializer.ScreenrecordFrameStream",
            side_effect=AssertionError("production path must not start a stream"),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: next(captures),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
        )

    assert result.success
    assert [label for label, _point in taps] == [
        f"level_skip:{EHLS}",
        f"level_skip:{EALS}",
    ]


def test_initializer_logs_why_before_its_tap_sequence(tmp_path, monkeypatch):
    action_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(action_log))
    initial = _frame(1)
    complete = _frame(3)
    captures = iter((complete,))

    def logged_tap(_point, *, label, verification):
        assert verification is not None
        log_input(f"Synthetic tap: {label}", console=False)
        return True

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch(
            "core.level_skip_initializer.detect_current_buy_quantity",
            return_value="max",
        ),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "affordable"),
                EALS: _box(EALS, "affordable"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: next(captures),
            tap_fn=logged_tap,
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
        )

    assert result.success
    action_lines = [
        line
        for line in action_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("[ACTION ")
    ]
    assert action_lines[0].endswith(
        "] Initializing level skips — maximize Enemy Health and Attack Level "
        "Skip before wave 40 so normal strategy actions can continue"
    )
    assert len(action_lines) == 1
    input_lines = [
        line
        for line in action_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("[INPUT ")
    ]
    assert input_lines[0].endswith(
        f"] Synthetic tap: level_skip:{EHLS}"
    )
    assert input_lines[1].endswith(
        f"] Synthetic tap: level_skip:{EALS}"
    )
    result_lines = [
        line
        for line in action_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("[RESULT ")
    ]
    assert len(result_lines) == 1
    assert "Level-skip initialization complete — EHLS and EALS maxed" in (
        result_lines[0]
    )


def test_fallback_reuses_verified_tap_authority_during_capture():
    capture_started = Event()
    concurrent_tap_seen = Event()

    def capture():
        capture_started.set()
        assert concurrent_tap_seen.wait(timeout=1.0)
        return _frame(2)

    def tap(_point, *, label, verification):
        assert label == "test"
        assert verification is sentinel
        if capture_started.is_set():
            concurrent_tap_seen.set()
        return True

    sentinel = object()
    frame, taps_sent, dispatch_ok = _tap_and_capture(
        point=(10, 20),
        label="test",
        capture_fn=capture,
        tap_fn=tap,
        verification=sentinel,
    )

    assert dispatch_ok
    assert taps_sent == 2
    assert frame is not None and int(frame[0, 0, 0]) == 2
    assert concurrent_tap_seen.is_set()


def test_fallback_caps_bursts_while_waiting_for_capture_feedback():
    initial = _frame(1)
    after_ehls = _frame(2)
    complete = _frame(3)
    capture_calls = 0
    taps = []
    ehls_burst_complete = Event()
    eals_burst_complete = Event()

    def capture():
        nonlocal capture_calls
        capture_calls += 1
        burst_complete = (
            ehls_burst_complete if capture_calls == 1 else eals_burst_complete
        )
        assert burst_complete.wait(timeout=1.0)
        return after_ehls if capture_calls == 1 else complete

    def tap(point, *, label, verification):
        taps.append((label, point))
        count = [item_label for item_label, _point in taps].count(label)
        if label == f"level_skip:{EHLS}" and count == 4:
            ehls_burst_complete.set()
        if label == f"level_skip:{EALS}" and count == EALS_TAPS_PER_BURST:
            eals_burst_complete.set()
        return True

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "affordable"),
                EALS: _box(EALS, "affordable"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=capture,
            tap_fn=tap,
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
        )

    assert result.success
    assert [label for label, _point in taps].count(
        f"level_skip:{EHLS}"
    ) == 4
    assert [label for label, _point in taps].count(
        f"level_skip:{EALS}"
    ) == EALS_TAPS_PER_BURST


def test_ehls_warmup_burst_forces_feedback_before_a_fifth_tap():
    initial = _frame(1)
    after_ehls = _frame(2)
    complete = _frame(3)
    captures = iter((after_ehls, complete))
    taps = []

    class WarmingStream:
        is_live = False
        failed = False
        age_s = 0.0

        def __init__(self):
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    stream = WarmingStream()

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={
                EHLS: _box(EHLS, "affordable"),
                EALS: _box(EALS, "affordable"),
            },
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: next(captures),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, stream.stopped)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=lambda: stream,
            eals_taps_per_burst=1,
        )

    assert result.success
    assert taps[:4] == [
        (f"level_skip:{EHLS}", False),
        (f"level_skip:{EHLS}", False),
        (f"level_skip:{EHLS}", False),
        (f"level_skip:{EHLS}", False),
    ]
    assert taps[4:] == [(f"level_skip:{EALS}", True)]
    assert stream.stopped


def test_initializer_finishes_without_taps_when_both_skips_start_gold_boxed():
    taps = []

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={EHLS: _box(EHLS, "maxed"), EALS: _box(EALS, "maxed")},
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            return_value=(True, {}),
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(1, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=_frame(1),
            capture_fn=lambda: (_ for _ in ()).throw(AssertionError("unexpected capture")),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
        )

    assert result.success
    assert result.taps_sent == 0
    assert taps == []


def test_initializer_skips_ehls_taps_when_only_ehls_starts_gold_boxed():
    initial = _frame(1)
    complete = _frame(2)
    taps = []

    def gold_box(frame, rect):
        is_ehls = rect[0] < 500
        return is_ehls or int(frame[0, 0, 0]) >= 2, {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={EHLS: _box(EHLS, "maxed"), EALS: _box(EALS, "affordable")},
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(1, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: complete,
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
        )

    assert result.success
    assert result.taps_sent == 1
    assert [label for label, _point in taps] == [f"level_skip:{EALS}"]


def test_initializer_does_not_tap_eals_when_it_starts_gold_boxed():
    initial = _frame(1)
    complete = _frame(2)
    taps = []

    def gold_box(frame, rect):
        is_ehls = rect[0] < 500
        return (int(frame[0, 0, 0]) >= 2 if is_ehls else True), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={EHLS: _box(EHLS, "affordable"), EALS: _box(EALS, "maxed")},
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(1, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: complete,
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=None,
            ehls_taps_per_burst=1,
            eals_taps_per_burst=1,
        )

    assert result.success
    assert result.taps_sent == 1
    assert [label for label, _point in taps] == [f"level_skip:{EHLS}"]
    assert result.eals_first_tap_elapsed_s is None


def test_live_stream_keeps_purchasing_without_blocking_screenshot_captures():
    initial = _frame(1)
    after_ehls = _frame(2)
    complete = _frame(3)
    taps = []

    class FakeStream:
        is_live = True
        failed = False
        age_s = 0.0

        def __init__(self):
            self.started = False
            self.stopped = False
            self.sequence = 0

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def latest_frame(self):
            self.sequence += 1
            labels = [label for label, _point in taps]
            if f"level_skip:{EALS}" in labels:
                return self.sequence, complete
            if labels.count(f"level_skip:{EHLS}") >= 2:
                return self.sequence, after_ehls
            return self.sequence, initial

    stream = FakeStream()

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={EHLS: _box(EHLS, "affordable"), EALS: _box(EALS, "affordable")},
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: (_ for _ in ()).throw(
                AssertionError("live stream path must not request a screenshot")
            ),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=lambda: stream,
        )

    assert result.success
    assert [label for label, _point in taps] == [
        f"level_skip:{EHLS}",
        f"level_skip:{EHLS}",
        f"level_skip:{EALS}",
    ]
    assert stream.started and stream.stopped


def test_live_stream_reuses_initial_verified_frame_until_new_frame_arrives():
    initial = _frame(1)
    after_ehls = _frame(2)
    complete = _frame(3)
    taps = []

    class RepeatingStream:
        is_live = True
        failed = False
        age_s = 0.0

        def __init__(self):
            self.calls = 0
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def latest_frame(self):
            self.calls += 1
            if self.calls < 4:
                return 1, initial
            if self.calls < 7:
                return 2, after_ehls
            return 3, complete

    stream = RepeatingStream()

    def gold_box(frame, rect):
        value = int(frame[0, 0, 0])
        is_ehls = rect[0] < 500
        return value >= (2 if is_ehls else 3), {}

    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "RUNNING", "menu": "UTILITY_MENU"},
        ),
        patch("core.level_skip_initializer.detect_current_buy_quantity", return_value="max"),
        patch(
            "core.level_skip_initializer._target_boxes",
            return_value={EHLS: _box(EHLS, "affordable"), EALS: _box(EALS, "affordable")},
        ),
        patch(
            "core.level_skip_initializer.evaluate_upgrade_box_gold_box",
            side_effect=gold_box,
        ),
        patch(
            "core.level_skip_initializer.detect_wave_number_from_image",
            return_value=(20, 99.0),
        ),
    ):
        result = initialize_level_skips(
            screenshot=initial,
            capture_fn=lambda: pytest.fail("live stream path must not capture"),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, stream.calls)) or True
            ),
            sleep_fn=lambda _seconds: None,
            frame_stream_factory=lambda: stream,
        )

    assert result.success
    assert taps == [
        (f"level_skip:{EHLS}", 1),
        (f"level_skip:{EHLS}", 2),
        (f"level_skip:{EHLS}", 3),
        (f"level_skip:{EALS}", 4),
        (f"level_skip:{EALS}", 5),
        (f"level_skip:{EALS}", 6),
    ]
    assert stream.stopped


def test_fast_initializer_refuses_non_running_screen_without_taps():
    taps = []
    with (
        patch(
            "core.level_skip_initializer.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN", "menu": None},
        ),
        patch("core.level_skip_initializer.log_result") as result_log,
    ):
        result = initialize_level_skips(
            screenshot=_frame(1),
            tap_fn=lambda point, *, label, verification: (
                taps.append((label, point)) or True
            ),
        )

    assert not result.success
    assert result.reason == "not_running"
    assert taps == []
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Level-skip initialization failed — not running"
    )


def test_executor_records_fast_initializer_metrics():
    ctx = MissionContext()
    ctx.data["mission_vars"] = {"last_detection_state": "RUNNING"}
    save_coordinator = Mock()
    ctx.data["player_save_preflight_coordinator"] = save_coordinator
    result = LevelSkipInitializationResult(
        success=True,
        ehls_maxed=True,
        eals_maxed=True,
        elapsed_s=5.25,
        ehls_wave=20,
        eals_wave=30,
        taps_sent=8,
        reason="complete",
        eals_first_tap_wave=20,
        eals_first_tap_elapsed_s=4.75,
    )

    def initialize_with_invalidation(**kwargs):
        kwargs["mutation_observer_fn"]()
        return result

    with patch(
        "core.action_executor.initialize_level_skips",
        side_effect=initialize_with_invalidation,
    ):
        execute_actions(
            _frame(1),
            [{"type": "level_skip_initialize", "_strategy": True}],
            ctx,
        )

    mv = ctx.data["mission_vars"]
    assert mv["ehls_completed"] is True
    assert mv["eals_completed"] is True
    assert mv["ehls_completion_wave"] == 20
    assert mv["eals_completion_wave"] == 30
    assert mv["eals_first_tap_wave"] == 20
    assert mv["eals_first_tap_elapsed_s"] == 4.75
    assert mv["level_skip_elapsed_s"] == 5.25
    assert mv["level_skip_taps_sent"] == 8
    save_coordinator.close_mapping_candidate_window.assert_called_once_with(
        "level_skip_mutation_started"
    )
    save_coordinator.invalidate.assert_not_called()
