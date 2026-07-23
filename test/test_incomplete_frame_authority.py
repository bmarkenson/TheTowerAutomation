from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.clickmap_access import get_clickmap, resolve_dot_path
from core.input import tap_if_visible
from core.label_tapper import get_label_match
from core.matcher import get_match, match_entry_result
from core.ss_capture import is_complete_screenshot
from core.state_detector import detect_state_and_overlays


ROOT = Path(__file__).resolve().parents[1]


def _mostly_black_game_over_frame():
    source = cv2.imread(
        str(ROOT / "test" / "fixtures" / "game_over_stats_20260715.png")
    )
    assert source is not None

    partial = np.zeros_like(source)
    # Reproduce a partial compositor result whose few rendered strips retain
    # both the Game Over identity and one actionable terminal control.
    partial[299:635, 299:802] = source[299:635, 299:802]
    partial[1337:1488, 552:1005] = source[1337:1488, 552:1005]
    return partial


def test_mostly_black_frame_can_match_actionable_evidence_but_not_authority():
    partial = _mostly_black_game_over_frame()
    clickmap = get_clickmap()
    entry = resolve_dot_path("buttons.home:game_over")

    state_point, state_confidence = get_match(
        "indicators.game_over",
        screenshot=partial,
    )
    action_match = match_entry_result(
        partial,
        entry,
        grayscale=True,
        padding=0,
        clickmap=clickmap,
    )

    assert float(np.mean(np.max(partial, axis=2) < 8)) > 0.8
    assert state_point is not None
    assert state_confidence >= 0.99
    assert action_match.matched
    assert not is_complete_screenshot(partial)

    assert detect_state_and_overlays(partial) == {
        "state": "UNKNOWN",
        "secondary_states": [],
        "overlays": [],
        "menu": None,
    }
    with pytest.raises(ValueError, match="refused an incomplete screenshot"):
        get_label_match("buttons.home:game_over", screenshot=partial)

    with patch("core.input._dispatch_tap") as dispatch:
        assert not tap_if_visible("buttons.home:game_over", screenshot=partial)
    dispatch.assert_not_called()


def test_game_over_home_control_covers_attack_dissonance_layout():
    template = cv2.imread(
        str(ROOT / "assets" / "match_templates" / "buttons" / "home:game_over.png")
    )
    assert template is not None
    height, width = template.shape[:2]
    frame = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    x, y = 564, 1443
    frame[y : y + height, x : x + width] = template

    assert get_label_match("buttons.home:game_over", screenshot=frame) == (
        x,
        y,
        width,
        height,
    )
