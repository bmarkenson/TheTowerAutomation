from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.battle_perks import ocr_latest_selected_perk
from core.perk_timeline import (
    PerkProgress,
    PerkTimelineObserver,
    PerkTimelineTracker,
    measure_perk_progress,
    timeline_perk_family,
)
from core.scrolling import ScrollCaptureResult, ScrollResult


FIXTURES = Path(__file__).parent / "fixtures"


def _progress(current: int, next_wave: int) -> PerkProgress:
    return PerkProgress(
        "scheduled",
        current,
        next_wave,
        f"{current} / {next_wave}",
        90.0,
    )


def _perk(
    display_text: str,
    *,
    color: str = "blue",
    confidence: float = 95.0,
) -> dict:
    return {
        "display_text": display_text,
        "color": color,
        "instance_model": (
            "leveled" if color == "blue" else "single_instance"
        ),
        "confidence": confidence,
    }


def _full(*perks: dict) -> dict:
    return {
        "selected": list(perks),
        "quality": {
            "source_complete": True,
            "valid": True,
        },
    }


def _stabilize(
    tracker: PerkTimelineTracker,
    progress: PerkProgress,
    *,
    wave: int,
):
    tracker.observe(progress, wave=wave)
    return tracker.observe(progress, wave=wave)


def test_measure_perk_progress_reads_retained_dynamic_top_bar():
    frame = cv2.imread(
        str(FIXTURES / "open_perks_dynamic_progress_20260723.png")
    )
    assert frame is not None

    progress = measure_perk_progress(frame)

    assert progress.status == "scheduled"
    assert progress.current_wave == 80
    assert progress.next_wave == 191


def test_measure_perk_progress_tolerates_ocr_artifacts_and_terminal_label():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    noisy = measure_perk_progress(
        frame,
        text_fn=lambda crop: ("3003)/°3025", 0.0),
    )
    complete = measure_perk_progress(
        frame,
        text_fn=lambda crop: ("View Perks", 93.0),
    )

    assert noisy.current_wave == 3003
    assert noisy.next_wave == 3025
    assert complete.status == "complete"


def test_tracker_records_pwr_cascades_as_atomic_batches_then_singletons():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observed_at = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    assert _stabilize(tracker, _progress(80, 100), wave=80) is None
    first = _stabilize(tracker, _progress(100, 142), wave=101)
    assert first is not None
    assert first.scheduled_wave == 100
    assert first.snapshot_mode == "full"
    assert tracker.record_full_snapshot(
        _full(
            _perk("Perk wave requirement -50.00%"),
            _perk("Increase max game speed by +0.50"),
        ),
        observed_at=observed_at,
    )

    second = _stabilize(tracker, _progress(142, 184), wave=143)
    assert second is not None
    assert tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
            _perk("Increase max game speed by +0.50"),
        ),
        observed_at=observed_at,
    )
    assert tracker.pwr_maxed

    third = _stabilize(tracker, _progress(184, 226), wave=185)
    assert third is not None
    assert third.snapshot_mode == "latest"
    assert tracker.record_latest(
        _perk("Defense percent +10.00%"),
        observed_at=observed_at,
    )

    snapshot = tracker.snapshot()
    assert len(snapshot["batches"]) == 3
    assert snapshot["batches"][0]["selection_model"] == "simultaneous_batch"
    assert {
        selection["family"]
        for selection in snapshot["batches"][0]["selections"]
    } == {"perk_wave_requirement", "max_game_speed"}
    assert {
        selection["family"]
        for selection in snapshot["batches"][1]["selections"]
    } == {"defense_percent", "perk_wave_requirement"}
    assert snapshot["batches"][2]["selection_model"] == (
        "singleton_after_pwr_max"
    )
    assert snapshot["batches"][2]["selections"][0]["before_display_text"] == (
        "Defense percent +5.00%"
    )


def test_mid_battle_attachment_establishes_baseline_without_inventing_waves():
    tracker = PerkTimelineTracker()

    request = _stabilize(tracker, _progress(500, 540), wave=500)

    assert request is not None
    assert request.kind == "baseline"
    assert request.scheduled_wave is None
    assert tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    snapshot = tracker.snapshot()
    assert snapshot["baseline_status"] == "observed_mid_battle"
    assert snapshot["batches"] == []
    assert snapshot["pwr_maxed_observed"] is True


def test_latest_perk_reader_uses_top_complete_row_and_color():
    frame = cv2.imread(
        str(
            FIXTURES
            / "ui_state_20260714"
            / "active_perks_selected_auto_pick_on.png"
        )
    )
    assert frame is not None

    latest = ocr_latest_selected_perk(
        frame,
        text_fn=lambda crop: ("Bounce Shot +2", 98.0),
    )

    assert latest is not None
    assert latest["display_text"] == "Bounce Shot +2"
    assert latest["color"] == "blue"
    assert latest["latest_selection_rank"] == 1


def test_family_mapping_distinguishes_enemy_health_from_regen_tradeoff():
    assert timeline_perk_family(
        "Enemies have -55.0% health, but tower health regen and lifesteal -90%"
    ) == "enemy_health_tradeoff"
    assert timeline_perk_family(
        "tower health regen x8.80, but tower max health -60%"
    ) == "tower_health_regen_tradeoff"


def test_observer_guards_each_panel_input_and_records_complete_batch():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guards = []
    taps = []

    def guard():
        guards.append("guard")
        return True

    def fake_scroll_to_edge(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_top:perks")
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_next:perks")
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
    ):
        navigated = observer.handle(
            running,
            {"state": "RUNNING"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=guard,
            progress_fn=lambda frame: _progress(100, 142),
            capture_fn=lambda: panel,
            detector=lambda frame: {"state": "PERKS"},
            safe_tap_fn=lambda key, **kwargs: taps.append(key) or True,
            tap_visible_fn=lambda key, **kwargs: taps.append(key) or True,
            swipe_fn=lambda key: taps.append(key) or True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -25.00%")
            ),
            sleep_fn=lambda seconds: None,
        )

    assert navigated is True
    assert taps == [
        "navigation.open_perks",
        "gesture_targets.goto_top:perks",
        "gesture_targets.goto_next:perks",
        "buttons.close:perks",
    ]
    assert len(guards) == 4
    assert tracker.snapshot()["batches"][0]["scheduled_wave"] == 100


def test_observer_leaves_owned_panel_open_when_pause_arrives_before_swipe():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guard_results = iter((True, False))
    closed = []

    navigated = observer.handle(
        running,
        {"state": "RUNNING"},
        wave=101,
        actions_allowed=True,
        action_guard_fn=lambda: next(guard_results),
        progress_fn=lambda frame: _progress(100, 142),
        capture_fn=lambda: panel,
        detector=lambda frame: {"state": "PERKS"},
        safe_tap_fn=lambda key, **kwargs: True,
        tap_visible_fn=lambda key, **kwargs: closed.append(key) or True,
        visible_fn=lambda key, **kwargs: True,
        swipe_fn=lambda key: True,
        sleep_fn=lambda seconds: None,
    )

    assert navigated is True
    assert closed == []
    assert tracker.pending is not None
    assert (
        observer.handle(
            panel,
            {"state": "PERKS"},
            wave=101,
            actions_allowed=False,
            action_guard_fn=lambda: False,
        )
        is False
    )


def test_observer_restores_panel_after_capture_completed_during_pause():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guard_results = iter((True, True, True, False))
    closed = []

    def fake_scroll_to_edge(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_top:perks")
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_next:perks")
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
    ):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=lambda: next(guard_results),
            progress_fn=lambda frame: _progress(100, 142),
            capture_fn=lambda: panel,
            detector=lambda frame: {"state": "PERKS"},
            safe_tap_fn=lambda key, **kwargs: True,
            tap_visible_fn=lambda key, **kwargs: closed.append(key) or True,
            swipe_fn=lambda key: True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -25.00%")
            ),
            sleep_fn=lambda seconds: None,
        )

    assert tracker.pending is None
    assert closed == []
    assert observer.handle(
        panel,
        {"state": "PERKS"},
        wave=101,
        actions_allowed=True,
        action_guard_fn=lambda: True,
        tap_visible_fn=lambda key, **kwargs: closed.append(key) or True,
    )
    assert closed == ["buttons.close:perks"]
