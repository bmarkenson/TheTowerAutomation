from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.matcher import MatchResult, _match_entry, match_entry_result


def _write_template(tmp_path, template):
    template_dir = tmp_path / "templates"
    template_path = template_dir / "indicators" / "sample.png"
    template_path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(template_path), template)
    return template_dir


def test_match_result_exposes_bbox_center_confidence_and_search_region(tmp_path):
    rng = np.random.default_rng(42)
    template = rng.integers(0, 256, size=(3, 4, 3), dtype=np.uint8)
    screenshot = np.zeros((20, 24, 3), dtype=np.uint8)
    screenshot[7:10, 8:12] = template
    template_dir = _write_template(tmp_path, template)
    entry = {
        "match_template": "indicators/sample.png",
        "match_region": {"x": 8, "y": 7, "w": 4, "h": 3},
        "match_padding": 2,
        "match_threshold": 0.9,
    }

    result = match_entry_result(screenshot, entry, template_dir)

    assert result.matched
    assert result.bbox == (8, 7, 4, 3)
    assert result.center == (10, 8)
    assert result.search_region == (6, 5, 8, 7)
    assert result.confidence == pytest.approx(1.0)

    point, confidence = _match_entry(screenshot, entry, template_dir)
    assert point == result.center
    assert confidence == result.confidence


def test_match_result_resolves_shared_region_from_supplied_clickmap(tmp_path):
    rng = np.random.default_rng(7)
    template = rng.integers(0, 256, size=(4, 5, 3), dtype=np.uint8)
    screenshot = np.zeros((18, 22, 3), dtype=np.uint8)
    screenshot[6:10, 9:14] = template
    template_dir = _write_template(tmp_path, template)
    entry = {
        "match_template": "indicators/sample.png",
        "region_ref": "sample_area",
        "match_threshold": 0.9,
    }
    clickmap = {
        "_shared_match_regions": {
            "sample_area": {
                "match_region": {"x": 9, "y": 6, "w": 5, "h": 4}
            }
        }
    }

    result = match_entry_result(
        screenshot,
        entry,
        template_dir,
        padding=0,
        clickmap=clickmap,
    )

    assert result.matched
    assert result.bbox == (9, 6, 5, 4)


def test_label_match_uses_shared_engine_with_legacy_profile():
    from core import label_tapper

    screenshot = np.zeros((20, 20, 3), dtype=np.uint8)
    entry = {
        "match_template": "buttons/example.png",
        "match_region": {"x": 1, "y": 2, "w": 3, "h": 4},
        "match_threshold": 0.9,
    }
    clickmap = {"buttons": {"example": entry}}
    result = MatchResult(
        bbox=(5, 6, 7, 8),
        confidence=0.97,
        threshold=0.9,
        search_region=(1, 2, 3, 4),
    )

    with (
        patch.object(label_tapper, "resolve_dot_path", return_value=entry),
        patch.object(label_tapper, "get_clickmap", return_value=clickmap),
        patch.object(label_tapper, "match_entry_result", return_value=result) as matcher,
    ):
        meta = label_tapper.get_label_match(
            "buttons.example",
            screenshot=screenshot,
            return_meta=True,
        )

    assert meta["x"] == 5
    assert meta["y"] == 6
    assert meta["w"] == 7
    assert meta["h"] == 8
    assert meta["match_score"] == 0.97
    matcher.assert_called_once_with(
        screenshot,
        entry,
        grayscale=True,
        padding=0,
        clickmap=clickmap,
    )
