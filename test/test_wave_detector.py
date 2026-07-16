from unittest.mock import patch

import numpy as np

from utils.wave_detector.pipeline import (
    _select_consensus,
    detect_wave_number_from_image,
)


def _candidate(value: int, confidence: float, tag: str):
    return value, confidence, tag, np.zeros((2, 2), dtype=np.uint8)


def test_consensus_prefers_repeated_visual_evidence():
    value, confidence, tag, image = _select_consensus(
        [
            _candidate(3502, 87.0, "white"),
            _candidate(3502, 92.0, "otsu"),
            _candidate(8502, 99.0, "off_target"),
        ]
    )

    assert value == 3502
    assert confidence == 89.5
    assert tag == "otsu"
    assert image is not None


def test_single_high_confidence_off_target_read_is_rejected():
    assert _select_consensus([_candidate(8502, 99.0, "off_target")]) == (
        None,
        -1.0,
        None,
        None,
    )


def test_tied_candidate_support_is_ambiguous():
    value, confidence, tag, image = _select_consensus(
        [
            _candidate(3502, 92.0, "a"),
            _candidate(3502, 91.0, "b"),
            _candidate(8502, 98.0, "c"),
            _candidate(8502, 97.0, "d"),
        ]
    )

    assert (value, confidence, tag, image) == (None, -1.0, None, None)


def test_detector_has_no_progression_rate_or_fixed_wave_ceiling():
    frame = np.zeros((12, 12, 3), dtype=np.uint8)

    with (
        patch(
            "utils.wave_detector.pipeline._detect_quick",
            return_value=(25000, 90.0, "quick", frame),
        ),
        patch("utils.wave_detector.pipeline._detect_heavy") as heavy,
    ):
        assert detect_wave_number_from_image(frame) == (25000, 90.0)

    heavy.assert_not_called()


def test_heavy_consensus_recovers_an_ambiguous_quick_pass():
    frame = np.zeros((12, 12, 3), dtype=np.uint8)

    with (
        patch(
            "utils.wave_detector.pipeline._detect_quick",
            return_value=(None, -1.0, None, None),
        ),
        patch(
            "utils.wave_detector.pipeline._detect_heavy",
            return_value=(4321, 88.0, "heavy", frame),
        ),
    ):
        assert detect_wave_number_from_image(frame) == (4321, 88.0)


def test_disagreeing_quick_and_heavy_consensus_is_rejected():
    frame = np.zeros((12, 12, 3), dtype=np.uint8)

    with (
        patch(
            "utils.wave_detector.pipeline._detect_quick",
            return_value=(3502, 90.0, "quick", frame),
        ),
        patch(
            "utils.wave_detector.pipeline._detect_heavy",
            return_value=(8502, 95.0, "heavy", frame),
        ),
    ):
        assert detect_wave_number_from_image(frame, use_heavy=True) == (None, -1.0)
