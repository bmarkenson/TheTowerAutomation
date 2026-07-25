from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np

from core.battle_activation_tracker import BattleActivationTracker
from core.matcher import MatchResult


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
    assert [
        event["approximate_wave"] for event in snapshot["nuke_activations"]
    ] == [3211, 3601]

    snapshot["nuke_activations"].clear()
    assert len(tracker.snapshot()["nuke_activations"]) == 2


def test_nonrunning_and_match_failures_cannot_confirm_disappearance():
    tracker = BattleActivationTracker()
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    visible = True
    failure_reason = None

    def match_button(dot_path, *, screenshot):
        del dot_path, screenshot
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
        ready_confirmation_frames=1,
        absence_confirmation_frames=1,
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    visible = True

    def match_button(dot_path, *, screenshot):
        del dot_path, screenshot
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
    assert tracker.snapshot()["nuke_activations"] == []
