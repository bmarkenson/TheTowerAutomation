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
    assert tracker.snapshot()["schema_version"] == 3


def test_tracker_records_every_confirmed_second_wind_wings_disappearance():
    wings_present = cv2.imread(
        str(FIXTURES / "running_menu_reward_badges_20260715.png")
    )
    wings_absent = cv2.imread(
        str(FIXTURES / "running_menu_no_reward_badges_20260715.png")
    )
    assert wings_present is not None
    assert wings_absent is not None
    tracker = BattleActivationTracker(
        second_wind_absence_confirmation_frames=2,
    )
    observed_at = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

    def match_visual(dot_path, *, screenshot):
        if dot_path.startswith("indicators.second_wind_"):
            return get_match_result(dot_path, screenshot=screenshot)
        return _match(visible=True)

    def observe(frame, wave, second):
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
        assert observe(wings_present, 4000, 0) == []
        assert observe(wings_present, 4001, 1) == []
        assert observe(wings_absent, 4010, 2) == []
        first = observe(wings_absent, 4015, 3)
        assert [event["ability"] for event in first] == ["second_wind"]
        assert first[0]["approximate_wave"] == 4010
        assert first[0]["detected_at"] == "2026-07-26T12:00:02+00:00"
        assert first[0]["confirmed_at"] == "2026-07-26T12:00:03+00:00"
        assert first[0]["detection_source"] == "tower_wings_disappearance"

        assert observe(wings_present, 4400, 4) == []
        assert observe(wings_present, 4401, 5) == []
        assert observe(wings_absent, 4410, 6) == []
        second = observe(wings_absent, 4415, 7)

    assert [event["sequence"] for event in second] == [2]
    snapshot = tracker.snapshot()
    assert [
        event["approximate_wave"]
        for event in snapshot["second_wind_activations"]
    ] == [4010, 4410]


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


def test_confirmed_activation_preserves_first_absent_frame_as_evidence():
    tracker = BattleActivationTracker(
        presence_confirmation_frames=1,
        absence_confirmation_frames=1,
        second_wind_absence_confirmation_frames=2,
    )
    wings_visible = True

    def match_visual(dot_path, *, screenshot):
        del screenshot
        if dot_path.startswith("indicators.second_wind_"):
            return _match(visible=wings_visible)
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
        first_absent = np.full((20, 20, 3), 17, dtype=np.uint8)
        assert tracker.observe(
            first_absent,
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
    assert np.array_equal(captures[0]["frame"], first_absent)
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
        second_wind_absence_confirmation_frames=1,
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
