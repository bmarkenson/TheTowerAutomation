from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.battle_activation_tracker import BattleActivationTracker
from core.matcher import MatchResult, get_match_result


FIXTURES = Path(__file__).parent / "fixtures"


def _match(*, visible: bool, failure_reason: str | None = None) -> MatchResult:
    confidence = 0.97 if visible else 0.42
    return MatchResult(
        bbox=(10, 20, 30, 40),
        confidence=confidence,
        threshold=0.9,
        search_region=(0, 0, 100, 100),
        failure_reason=failure_reason,
    )


def test_tracker_records_first_demon_mode_and_every_rearmed_nuke():
    tracker = BattleActivationTracker()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    visible = {"demon_mode": True, "nuke": True}
    observed_at = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    wave_observed_at = datetime(2026, 7, 25, 11, 59, 59, tzinfo=timezone.utc)

    def match_button(dot_path, *, screenshot):
        del screenshot
        if dot_path.startswith("indicators.second_wind_"):
            return _match(visible=False)
        name = dot_path.rsplit(".", 1)[-1]
        return _match(visible=visible[name])

    def observe(wave):
        return tracker.observe(
            frame,
            ui_state="RUNNING",
            wave=wave,
            wave_confidence=98.4,
            wave_observed_at=wave_observed_at,
            observed_at=observed_at,
        )

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_button,
    ):
        assert observe(3200) == []
        assert observe(3201) == []

        visible.update(demon_mode=False, nuke=False)
        assert observe(3210) == []
        events = observe(3211)

        assert [event["ability"] for event in events] == [
            "demon_mode",
            "nuke",
        ]
        assert events[0]["approximate_wave"] == 3211
        assert events[0]["wave_confidence"] == 98.4
        assert events[0]["wave_observed_at"] == "2026-07-25T11:59:59+00:00"
        assert events[0]["detected_at"] == "2026-07-25T12:00:00+00:00"
        assert events[1]["sequence"] == 1
        assert observe(3212) == []

        visible.update(demon_mode=True, nuke=True)
        assert observe(3590) == []
        assert observe(3591) == []
        visible.update(demon_mode=False, nuke=False)
        assert observe(3600) == []
        second_nuke = observe(3601)

    assert len(second_nuke) == 1
    assert second_nuke[0]["ability"] == "nuke"
    assert second_nuke[0]["sequence"] == 2
    snapshot = tracker.snapshot()
    assert snapshot["demon_mode_first_activation"]["approximate_wave"] == 3211
    assert snapshot["second_wind_activations"] == []
    assert [
        event["approximate_wave"] for event in snapshot["nuke_activations"]
    ] == [3211, 3601]
    assert [
        capture["ability"] for capture in tracker.drain_evidence_captures()
    ] == ["demon_mode", "nuke", "nuke"]

    snapshot["nuke_activations"].clear()
    assert len(tracker.snapshot()["nuke_activations"]) == 2


def test_nonrunning_and_match_failures_cannot_confirm_disappearance():
    tracker = BattleActivationTracker()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    visible = True
    failure_reason = None

    def match_button(dot_path, *, screenshot):
        del screenshot
        if dot_path.startswith("indicators.second_wind_"):
            return _match(visible=False)
        return _match(visible=visible, failure_reason=failure_reason)

    def observe(state="RUNNING"):
        return tracker.observe(
            frame,
            ui_state=state,
            wave=5000,
            wave_confidence=95.0,
            wave_observed_at=None,
        )

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_button,
    ):
        assert observe() == []
        assert observe() == []
        visible = False
        assert observe() == []
        assert observe("PERKS") == []
        assert observe() == []

        failure_reason = "template unavailable"
        assert observe() == []
        failure_reason = None
        assert observe() == []
        events = observe()

    assert [event["ability"] for event in events] == ["demon_mode", "nuke"]


def test_reset_clears_prior_battle_events_and_arming():
    tracker = BattleActivationTracker(
        presence_confirmation_frames=1,
        absence_confirmation_frames=1,
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    visible = True

    def match_button(dot_path, *, screenshot):
        del screenshot
        if dot_path.startswith("indicators.second_wind_"):
            return _match(visible=False)
        return _match(visible=visible)

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_button,
    ):
        tracker.observe(
            frame,
            ui_state="RUNNING",
            wave=100,
            wave_confidence=90.0,
            wave_observed_at=None,
        )
        visible = False
        assert len(
            tracker.observe(
                frame,
                ui_state="RUNNING",
                wave=101,
                wave_confidence=90.0,
                wave_observed_at=None,
            )
        ) == 2

    tracker.reset()
    assert tracker.snapshot()["demon_mode_first_activation"] is None
    assert tracker.snapshot()["second_wind_activations"] == []
    assert tracker.snapshot()["nuke_activations"] == []


def test_intro_sprint_disabled_buttons_register_as_present():
    """Live wave-100 evidence must arm both buttons before they disappear."""

    strip = cv2.imread(
        str(
            FIXTURES
            / "intro_sprint_disabled_floating_buttons_20260725_wave100.png"
        )
    )
    assert strip is not None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[790:903, 7:915] = strip
    missing = np.zeros_like(frame)
    tracker = BattleActivationTracker()

    assert tracker.observe(
        frame,
        ui_state="RUNNING",
        wave=100,
        wave_confidence=99.0,
        wave_observed_at=None,
    ) == []
    assert tracker.observe(
        frame,
        ui_state="RUNNING",
        wave=110,
        wave_confidence=99.0,
        wave_observed_at=None,
    ) == []
    assert tracker.observe(
        missing,
        ui_state="RUNNING",
        wave=120,
        wave_confidence=99.0,
        wave_observed_at=None,
    ) == []
    events = tracker.observe(
        missing,
        ui_state="RUNNING",
        wave=130,
        wave_confidence=99.0,
        wave_observed_at=None,
    )

    assert [event["ability"] for event in events] == ["demon_mode", "nuke"]
    assert events[0]["presence_confidence"] >= 0.8
    assert events[1]["presence_confidence"] >= 0.9
    assert tracker.snapshot()["schema_version"] == 4


def test_tracker_records_active_second_wind_icon_and_rearm_estimate():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    tracker = BattleActivationTracker(
        second_wind_active_confirmation_frames=1,
    )
    observed_at = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    wings_visible = True
    active_visible = False

    def match_visual(dot_path, *, screenshot):
        del screenshot
        if dot_path.endswith(("_left_wing", "_right_wing")):
            return _match(visible=wings_visible)
        if dot_path == "indicators.second_wind_active":
            return _match(visible=active_visible)
        return _match(visible=True)

    def observe(wave, second):
        return tracker.observe(
            frame,
            ui_state="RUNNING",
            wave=wave,
            wave_confidence=97.5,
            wave_observed_at=observed_at,
            observed_at=observed_at.replace(second=second),
        )

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_visual,
    ):
        assert observe(4000, 0) == []
        assert observe(4001, 1) == []

        wings_visible = False
        assert observe(4010, 2) == []
        assert observe(4011, 3) == []

        active_visible = True
        first = observe(4015, 4)
        assert [event["ability"] for event in first] == ["second_wind"]
        assert first[0]["approximate_wave"] == 4015
        assert first[0]["estimated_rearm_wave"] == 4415
        assert first[0]["rearm_wave_offset"] == 400
        assert first[0]["rearm_estimate_is_approximate"]
        assert first[0]["detected_at"] == "2026-07-26T12:00:04+00:00"
        assert first[0]["confirmed_at"] == "2026-07-26T12:00:04+00:00"
        assert first[0]["detection_source"] == "active_status_icon"
        assert first[0]["active_icon_confidence"] == 0.97
        assert observe(4016, 5) == []

        active_visible = False
        wings_visible = True
        assert observe(4415, 6) == []
        assert observe(4416, 7) == []
        wings_visible = False
        active_visible = True
        second = observe(4420, 8)

    assert [event["sequence"] for event in second] == [2]
    snapshot = tracker.snapshot()
    assert [
        event["approximate_wave"]
        for event in snapshot["second_wind_activations"]
    ] == [4015, 4420]
    assert [
        event["estimated_rearm_wave"]
        for event in snapshot["second_wind_activations"]
    ] == [4415, 4820]


def test_current_live_second_wind_wings_match_calibrated_regions():
    crop = cv2.imread(
        str(FIXTURES / "second_wind_wings_live_20260727.png")
    )
    assert crop is not None
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[430:530, 470:610] = crop

    left = get_match_result(
        "indicators.second_wind_left_wing",
        screenshot=frame,
    )
    right = get_match_result(
        "indicators.second_wind_right_wing",
        screenshot=frame,
    )

    assert left.matched
    assert right.matched
    assert left.confidence >= 0.65
    assert right.confidence >= 0.85


def test_second_wind_active_icon_matches_both_countdown_stages_only():
    early_crop = cv2.imread(
        str(FIXTURES / "second_wind_active_icon_early_20260728.png")
    )
    late_crop = cv2.imread(
        str(FIXTURES / "second_wind_active_icon_late_20260728.png")
    )
    busy_crop = cv2.imread(
        str(FIXTURES / "second_wind_active_icon_busy_20260728.png")
    )
    absent_crop = cv2.imread(
        str(FIXTURES / "second_wind_active_icon_absent_20260728.png")
    )
    assert early_crop is not None
    assert late_crop is not None
    assert busy_crop is not None
    assert absent_crop is not None

    def match(crop):
        frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        frame[675:800, 20:150] = crop
        return get_match_result(
            "indicators.second_wind_active",
            screenshot=frame,
        )

    early = match(early_crop)
    late = match(late_crop)
    busy = match(busy_crop)
    absent = match(absent_crop)

    assert early.matched
    assert early.confidence >= 0.99
    assert late.matched
    assert late.confidence >= 0.95
    assert busy.matched
    assert busy.confidence >= 0.85
    assert not absent.matched
    assert absent.confidence < 0.4


def test_one_visible_wing_disproves_second_wind_activation():
    armed_crop = cv2.imread(
        str(FIXTURES / "second_wind_wings_live_20260727.png")
    )
    occluded_crop = cv2.imread(
        str(FIXTURES / "second_wind_one_wing_occluded_20260728.png")
    )
    assert armed_crop is not None
    assert occluded_crop is not None

    armed_frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    armed_frame[430:530, 470:610] = armed_crop
    occluded_frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    occluded_frame[430:530, 470:610] = occluded_crop

    left = get_match_result(
        "indicators.second_wind_left_wing",
        screenshot=occluded_frame,
    )
    right = get_match_result(
        "indicators.second_wind_right_wing",
        screenshot=occluded_frame,
    )
    assert left.matched
    assert not right.matched

    tracker = BattleActivationTracker(
        presence_confirmation_frames=1,
        second_wind_active_confirmation_frames=1,
    )

    def match_visual(dot_path, *, screenshot):
        if dot_path.startswith("indicators.second_wind_"):
            return get_match_result(dot_path, screenshot=screenshot)
        return _match(visible=True)

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_visual,
    ):
        assert tracker.observe(
            armed_frame,
            ui_state="RUNNING",
            wave=2200,
            wave_confidence=98.0,
            wave_observed_at=None,
        ) == []
        for wave in range(2219, 2223):
            assert tracker.observe(
                occluded_frame,
                ui_state="RUNNING",
                wave=wave,
                wave_confidence=98.0,
                wave_observed_at=None,
            ) == []

    assert tracker.snapshot()["second_wind_activations"] == []
    assert tracker.drain_evidence_captures() == []


def test_confirmed_activation_preserves_first_absent_frame_as_evidence():
    tracker = BattleActivationTracker(
        presence_confirmation_frames=1,
        absence_confirmation_frames=1,
        second_wind_active_confirmation_frames=2,
    )
    wings_visible = True
    active_visible = False

    def match_visual(dot_path, *, screenshot):
        del screenshot
        if dot_path.endswith(("_left_wing", "_right_wing")):
            return _match(visible=wings_visible)
        if dot_path == "indicators.second_wind_active":
            return _match(visible=active_visible)
        return _match(visible=True)

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_visual,
    ):
        tracker.observe(
            np.zeros((20, 20, 3), dtype=np.uint8),
            ui_state="RUNNING",
            wave=5000,
            wave_confidence=99.0,
            wave_observed_at=None,
        )
        wings_visible = False
        active_visible = True
        first_active = np.full((20, 20, 3), 17, dtype=np.uint8)
        assert tracker.observe(
            first_active,
            ui_state="RUNNING",
            wave=5010,
            wave_confidence=98.0,
            wave_observed_at=None,
        ) == []
        events = tracker.observe(
            np.full((20, 20, 3), 99, dtype=np.uint8),
            ui_state="RUNNING",
            wave=5011,
            wave_confidence=97.0,
            wave_observed_at=None,
        )

    assert [event["ability"] for event in events] == ["second_wind"]
    captures = tracker.drain_evidence_captures()
    assert len(captures) == 1
    assert captures[0]["ability"] == "second_wind"
    assert np.array_equal(captures[0]["frame"], first_active)
    assert tracker.drain_evidence_captures() == []
    assert tracker.record_evidence_image(
        "second_wind",
        1,
        "screenshots/matches/second_wind.png",
    )
    assert (
        tracker.snapshot()["second_wind_activations"][0]["evidence_image"]
        == "screenshots/matches/second_wind.png"
    )


def test_second_wind_cannot_activate_without_first_observing_wings():
    wings_absent = cv2.imread(
        str(FIXTURES / "running_menu_no_reward_badges_20260715.png")
    )
    assert wings_absent is not None
    tracker = BattleActivationTracker(
        presence_confirmation_frames=1,
        second_wind_active_confirmation_frames=1,
    )

    def match_visual(dot_path, *, screenshot):
        if dot_path.startswith("indicators.second_wind_"):
            return get_match_result(dot_path, screenshot=screenshot)
        return _match(visible=True)

    with patch(
        "core.battle_activation_tracker.get_match_result",
        side_effect=match_visual,
    ):
        for wave in range(5000, 5005):
            events = tracker.observe(
                wings_absent,
                ui_state="RUNNING",
                wave=wave,
                wave_confidence=96.0,
                wave_observed_at=None,
            )
            assert events == []

    assert tracker.snapshot()["second_wind_activations"] == []
