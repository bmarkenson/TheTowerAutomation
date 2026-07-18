from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np

from core.matcher import get_match
from core.poison_swamp_stun import (
    PoisonSwampStunError,
    PoisonSwampStunEvidence,
    PoisonSwampStunState,
    ensure_poison_swamp_stun_off,
    measure_poison_swamp_stun,
)
from core.state_detector import detect_state_and_overlays
from core.upgrade_box_detector import UpgradeBox


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "gc_poison_swamp_stun_20260717"


def _load(name: str):
    frame = cv2.imread(str(FIXTURES / name))
    assert frame is not None, name
    return frame


def _frame(value: int):
    return np.full((1920, 1080, 3), value, dtype=np.uint8)


def _evidence(state: PoisonSwampStunState):
    return PoisonSwampStunEvidence(
        state=state,
        detail_visible=True,
        detail_confidence=1.0,
        off_confidence=1.0 if state is PoisonSwampStunState.OFF else 0.7,
        on_confidence=1.0 if state is PoisonSwampStunState.ON else 0.7,
    )


def test_live_detail_templates_separate_stun_off_and_on():
    off = _load("stun_off.png")
    on = _load("stun_on.png")

    off_evidence = measure_poison_swamp_stun(off)
    on_evidence = measure_poison_swamp_stun(on)

    assert off_evidence.state is PoisonSwampStunState.OFF
    assert on_evidence.state is PoisonSwampStunState.ON
    assert off_evidence.on_confidence < 0.9
    assert on_evidence.off_confidence < 0.9
    assert get_match("overlays.poison_swamp_detail", screenshot=off)[0] == (542, 297)
    assert get_match("indicators.poison_swamp_stun_off", screenshot=off)[0] == (
        910,
        1620,
    )
    assert get_match("buttons.poison_swamp_stun_on", screenshot=on)[0] == (
        910,
        1620,
    )
    for frame in (off, on):
        detection = detect_state_and_overlays(frame)
        assert detection["state"] == "UPGRADE_DETAIL"
        assert "UPGRADE_DETAIL" in detection["overlays"]


def test_guarded_correction_toggles_on_to_off_and_reverifies():
    uw = _frame(10)
    detail_on = _frame(20)
    detail_off = _frame(30)
    cleared = _frame(40)
    captures = iter((uw, detail_on, detail_off))
    safe_tap = Mock(return_value=True)
    tap_visible = Mock(return_value=True)

    def detector(frame):
        if int(frame[0, 0, 0]) in {10, 40}:
            return {"state": "RUNNING", "menu": "UW_MENU", "overlays": []}
        return {"state": "UPGRADE_DETAIL", "menu": None, "overlays": ["UPGRADE_DETAIL"]}

    def measure(frame):
        value = int(frame[0, 0, 0])
        return _evidence(
            PoisonSwampStunState.ON
            if value == 20
            else PoisonSwampStunState.OFF
        )

    result = ensure_poison_swamp_stun_off(
        capture_fn=lambda: next(captures),
        detector=detector,
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": [UpgradeBox("left", (26, 1367, 511, 246), text="Poison Swamp")],
            "right": [],
        },
        safe_tap_fn=safe_tap,
        tap_visible_fn=tap_visible,
        dismiss_fn=lambda **_kwargs: cleared,
        measure_fn=measure,
        sleep_fn=lambda _seconds: None,
    )

    assert result.changed
    assert result.evidence.state is PoisonSwampStunState.OFF
    safe_tap.assert_called_once_with(
        (179, 1428),
        require_visible=False,
        dispatch="now",
        log_label="uw_detail:Poison Swamp",
    )
    tap_visible.assert_called_once_with(
        "buttons.poison_swamp_stun_on",
        screenshot=detail_on,
    )


def test_guarded_correction_leaves_verified_off_state_unchanged():
    uw = _frame(10)
    detail_off = _frame(30)
    cleared = _frame(40)
    captures = iter((uw, detail_off))
    tap_visible = Mock()

    result = ensure_poison_swamp_stun_off(
        capture_fn=lambda: next(captures),
        detector=lambda frame: {
            "state": "RUNNING" if int(frame[0, 0, 0]) in {10, 40} else "UPGRADE_DETAIL",
            "menu": "UW_MENU" if int(frame[0, 0, 0]) in {10, 40} else None,
            "overlays": [],
        },
        detect_boxes_fn=lambda _frame, **_kwargs: {
            "left": [UpgradeBox("left", (26, 1367, 511, 246), text="Poison Swamp")],
            "right": [],
        },
        safe_tap_fn=Mock(return_value=True),
        tap_visible_fn=tap_visible,
        dismiss_fn=lambda **_kwargs: cleared,
        measure_fn=lambda _frame: _evidence(PoisonSwampStunState.OFF),
        sleep_fn=lambda _seconds: None,
    )

    assert not result.changed
    assert result.evidence.state is PoisonSwampStunState.OFF
    tap_visible.assert_not_called()


def test_guarded_correction_blocks_ambiguous_control_without_toggling():
    uw = _frame(10)
    ambiguous = _frame(20)
    captures = iter((uw, *([ambiguous] * 16)))
    tap_visible = Mock()
    dismiss = Mock()

    with np.testing.assert_raises_regex(
        PoisonSwampStunError,
        "timed out verifying Poison Swamp Stun",
    ):
        ensure_poison_swamp_stun_off(
            capture_fn=lambda: next(captures),
            detector=lambda _frame: {
                "state": "RUNNING",
                "menu": "UW_MENU",
                "overlays": [],
            },
            detect_boxes_fn=lambda _frame, **_kwargs: {
                "left": [
                    UpgradeBox(
                        "left",
                        (26, 1367, 511, 246),
                        text="Poison Swamp",
                    )
                ],
                "right": [],
            },
            safe_tap_fn=Mock(return_value=True),
            tap_visible_fn=tap_visible,
            dismiss_fn=dismiss,
            measure_fn=lambda _frame: _evidence(PoisonSwampStunState.UNKNOWN),
            sleep_fn=lambda _seconds: None,
        )

    tap_visible.assert_not_called()
    dismiss.assert_not_called()


def test_guarded_correction_rejects_incomplete_source_before_any_action():
    safe_tap = Mock()

    with np.testing.assert_raises_regex(
        PoisonSwampStunError,
        "capture was incomplete",
    ):
        ensure_poison_swamp_stun_off(
            capture_fn=lambda: np.zeros((1920, 1080, 3), dtype=np.uint8),
            safe_tap_fn=safe_tap,
            sleep_fn=lambda _seconds: None,
        )

    safe_tap.assert_not_called()
