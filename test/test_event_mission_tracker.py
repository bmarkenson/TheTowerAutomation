from __future__ import annotations

from unittest.mock import patch

import cv2
import numpy as np

import core.event_missions as event_missions
from core.event_missions import (
    EventMissionInventory,
    EventMissionObservation,
    ocr_event_mission_inventory,
)
from core.event_mission_tracker import (
    EventMissionTracker,
    STALLED_AFTER_SECONDS,
    WARNING_AFTER_SECONDS,
    WARNING_REPEAT_SECONDS,
    format_warning,
)


def _fixture(name: str):
    image = cv2.imread(f"test/fixtures/{name}")
    assert image is not None
    return image


def _inventory(*missions, remaining_seconds=7 * 24 * 3600):
    return EventMissionInventory(
        event_name="PRISMATIC LINES",
        remaining_seconds=remaining_seconds,
        missions=tuple(missions),
        complete=True,
        source_reason="test",
    )


def _mission(name="Reach wave 20 without any card equipped", progress=(0, 20)):
    return EventMissionObservation(
        name=name,
        incomplete=True,
        progress_current=progress[0] if progress else None,
        progress_target=progress[1] if progress else None,
        confidence=95.0,
    )


def test_fixture_ocr_reads_named_incomplete_event_missions_and_progress():
    inventory = ocr_event_mission_inventory(
        (_fixture("event_missions_20260713.png"),),
        complete=True,
        source_reason="fixture",
    )

    assert inventory.complete
    assert inventory.event_name == "PRISMATIC LINES"
    assert inventory.remaining_seconds == (6 * 24 + 21) * 3600
    observed = [
        (mission.name, mission.incomplete, mission.progress)
        for mission in inventory.missions
    ]
    assert observed == [
        ("Get the free gem reward in store 10 times", True, "8/10"),
        ("Login for 10 days", True, "8/10"),
    ]


def test_fixture_ocr_marks_claim_buttons_as_not_incomplete():
    inventory = ocr_event_mission_inventory(
        (_fixture("event_missions_claimable_20260715.png"),),
        complete=True,
        source_reason="fixture",
    )

    assert inventory.complete
    assert len(inventory.missions) == 2
    assert all(not mission.incomplete for mission in inventory.missions)


def test_progress_signature_supports_abbreviated_large_counts():
    row = np.zeros((241, 1046, 3), dtype=np.uint8)
    with patch.object(
        event_missions,
        "ocr_text_and_conf",
        side_effect=[
            ("Kill enemies", 95.0),
            ("Kill enemies 30", 90.0),
            ("1.25M/2.50M", 90.0),
        ],
    ):
        observation = event_missions._ocr_row(row)

    assert observation is not None
    assert observation.progress == "1.25M/2.50M"


def test_tracker_warns_after_age_and_stall_thresholds_and_repeats(tmp_path):
    tracker = EventMissionTracker(tmp_path / "event_missions.json")
    start = 1_000_000.0
    assert tracker.record_inventory(_inventory(_mission()), now=start)

    assert not tracker.due_warnings(now=start + WARNING_AFTER_SECONDS - 1)
    warnings = tracker.due_warnings(now=start + WARNING_AFTER_SECONDS)
    assert len(warnings) == 1
    assert warnings[0].progress == "0/20"
    assert "[EVENT_MISSION_WARNING]" in format_warning(warnings[0])
    assert not tracker.due_warnings(
        now=start + WARNING_AFTER_SECONDS + WARNING_REPEAT_SECONDS - 1
    )
    assert len(
        tracker.due_warnings(
            now=start + WARNING_AFTER_SECONDS + WARNING_REPEAT_SECONDS
        )
    ) == 1


def test_observed_progress_resets_stall_age_but_preserves_first_seen(tmp_path):
    state = tmp_path / "event_missions.json"
    start = 2_000_000.0
    tracker = EventMissionTracker(state)
    tracker.record_inventory(_inventory(_mission(progress=(0, 20))), now=start)
    tracker.record_inventory(
        _inventory(_mission(progress=(1, 20))),
        now=start + WARNING_AFTER_SECONDS,
    )

    reloaded = EventMissionTracker(state)
    assert not reloaded.due_warnings(
        now=start + WARNING_AFTER_SECONDS + STALLED_AFTER_SECONDS - 1
    )
    assert len(
        reloaded.due_warnings(
            now=start + WARNING_AFTER_SECONDS + STALLED_AFTER_SECONDS
        )
    ) == 1


def test_authoritative_completed_inventory_removes_old_warning(tmp_path):
    tracker = EventMissionTracker(tmp_path / "event_missions.json")
    start = 3_000_000.0
    mission = _mission()
    tracker.record_inventory(_inventory(mission), now=start)
    completed = EventMissionObservation(
        name=mission.name,
        incomplete=False,
        confidence=95.0,
    )
    tracker.record_inventory(_inventory(completed), now=start + WARNING_AFTER_SECONDS)

    assert not tracker.due_warnings(
        now=start + WARNING_AFTER_SECONDS + STALLED_AFTER_SECONDS
    )


def test_incomplete_ocr_inventory_does_not_replace_persisted_state(tmp_path):
    tracker = EventMissionTracker(tmp_path / "event_missions.json")
    start = 4_000_000.0
    tracker.record_inventory(_inventory(_mission()), now=start)
    rejected = EventMissionInventory(
        event_name="PRISMATIC LINES",
        remaining_seconds=None,
        missions=(),
        complete=False,
        source_reason="ocr_incomplete",
    )

    assert not tracker.record_inventory(rejected, now=start + 100)
    assert len(tracker.due_warnings(now=start + WARNING_AFTER_SECONDS)) == 1


def test_authoritative_inventory_preserves_a_row_missed_by_ocr(tmp_path):
    tracker = EventMissionTracker(tmp_path / "event_missions.json")
    start = 5_000_000.0
    specialized = _mission()
    tracker.record_inventory(_inventory(specialized), now=start)
    tracker.record_inventory(
        _inventory(_mission(name="Login for 10 days", progress=(3, 10))),
        now=start + 24 * 3600,
    )

    warnings = tracker.due_warnings(now=start + WARNING_AFTER_SECONDS)
    assert [warning.name for warning in warnings] == [specialized.name]


def test_same_named_event_resets_after_the_prior_event_expired(tmp_path):
    tracker = EventMissionTracker(tmp_path / "event_missions.json")
    start = 6_000_000.0
    tracker.record_inventory(
        _inventory(_mission(), remaining_seconds=3600),
        now=start,
    )
    tracker.record_inventory(
        _inventory(_mission(), remaining_seconds=7 * 24 * 3600),
        # The stored deadline includes one extra hour for the truncated UI.
        now=start + 2 * 3600,
    )

    assert not tracker.due_warnings(now=start + WARNING_AFTER_SECONDS)
