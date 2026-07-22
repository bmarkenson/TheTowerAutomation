from datetime import datetime, timezone
from unittest.mock import patch

import cv2
import numpy as np

from core.no_strategy_observer import (
    ATTACK_DISSONANCE_BADGE_REGION,
    NoStrategyRunObserver,
    OBSERVED_FIELDS,
    detect_attack_dissonance_badge,
)


def _clock():
    return datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)


def test_attack_dissonance_badge_is_localized_to_tier_badge_region():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    x, y, width, height = ATTACK_DISSONANCE_BADGE_REGION
    frame[y + 10 : y + height - 10, x + 10 : x + width - 10] = purple

    evidence = detect_attack_dissonance_badge(frame)

    assert evidence["observed"] is True
    assert evidence["purple_pixels"] == 3600


def test_purple_pixels_outside_badge_do_not_invent_dissonance():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[400:800, 100:500] = (255, 0, 255)

    evidence = detect_attack_dissonance_badge(frame)

    assert evidence["observed"] is False
    assert evidence["purple_pixels"] == 0


def test_no_strategy_observer_records_dissonance_as_observed_not_configured():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    x, y, width, height = ATTACK_DISSONANCE_BADGE_REGION
    frame[y : y + height, x : x + width] = purple
    observer = NoStrategyRunObserver(clock=_clock)

    observer.observe(
        frame,
        {"state": "RUNNING", "menu": "UTILITY_MENU", "secondary_states": []},
    )
    snapshot = observer.snapshot()

    identity = snapshot["fields"]["run_identity"]
    assert identity["status"] == "observed"
    assert identity["value"]["label"] == "Attack Dissonance"
    assert identity["source"] == "tier_attack_dissonance_badge"
    assert identity["phase"] == "in_battle"
    assert snapshot["coverage"] == {
        "observed": 1,
        "evidence_captured": 0,
        "total": len(OBSERVED_FIELDS),
        "complete": False,
    }


def test_post_run_value_preserves_home_phase_and_observation_time():
    observer = NoStrategyRunObserver(clock=_clock)

    observer.record_post_run_value(
        "free_upgrade_locks",
        {"Shockwave Size": "checked"},
        source="home_workshop_lock_details",
        observed_at="2026-07-22T20:00:00-07:00",
    )
    snapshot = observer.snapshot(finalized=True)

    locks = snapshot["fields"]["free_upgrade_locks"]
    assert locks == {
        "status": "observed",
        "value": {"Shockwave Size": "checked"},
        "source": "home_workshop_lock_details",
        "phase": "post_run_home",
        "confidence": "high",
        "observed_at": "2026-07-22T20:00:00-07:00",
    }
    assert snapshot["finalized"] is True
    assert snapshot["finalized_at"] == "2026-07-22T16:00:00+00:00"


def test_ultimate_weapon_observations_merge_across_visible_scroll_positions():
    observer = NoStrategyRunObserver(clock=_clock)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    first = type("Box", (), {"text": "Poison Swamp", "toggles": {"primary": "on"}})()
    second = type("Box", (), {"text": "Poison Swamp", "toggles": {"stun": "off"}})()

    with patch(
        "core.no_strategy_observer.detect_visible_boxes",
        side_effect=[{"left": [first]}, {"right": [second]}],
    ):
        detection = {"state": "RUNNING", "menu": "UW_MENU"}
        observer.observe(frame, detection)
        observer.observe(frame, detection)

    field = observer.snapshot()["fields"]["ultimate_weapons"]
    assert field["value"] == {"Poison Swamp": {"primary": "on", "stun": "off"}}


def test_unknown_post_run_field_is_rejected():
    observer = NoStrategyRunObserver(clock=_clock)

    try:
        observer.record_post_run_value("profile", "farm", source="guess")
    except ValueError as exc:
        assert "unknown No Strategy observation field" in str(exc)
    else:
        raise AssertionError("unknown observation field was accepted")
