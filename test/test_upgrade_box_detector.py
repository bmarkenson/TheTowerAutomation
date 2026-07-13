import cv2
import numpy as np

from core.upgrade_box_detector import evaluate_upgrade_box_gold_box


def _frame_and_rect():
    return np.zeros((260, 520, 3), dtype=np.uint8), (40, 30, 420, 200)


def test_gold_box_detector_recognizes_rectangular_max_border():
    frame, rect = _frame_and_rect()
    x, y, w, h = rect
    cv2.rectangle(
        frame,
        (x + int(w * 0.60), y + int(h * 0.64)),
        (x + int(w * 0.96), y + int(h * 0.94)),
        (41, 116, 116),
        thickness=5,
    )

    is_gold_boxed, metrics = evaluate_upgrade_box_gold_box(frame, rect)

    assert is_gold_boxed
    assert metrics["gold_pixel_ratio"] >= 0.04
    assert metrics["gold_row_ratio"] >= 0.60
    assert metrics["gold_column_ratio"] >= 0.45


def test_gold_box_detector_rejects_blue_affordable_button():
    frame, rect = _frame_and_rect()
    x, y, w, h = rect
    cv2.rectangle(
        frame,
        (x + int(w * 0.60), y + int(h * 0.64)),
        (x + int(w * 0.96), y + int(h * 0.94)),
        (93, 58, 17),
        thickness=-1,
    )

    is_gold_boxed, metrics = evaluate_upgrade_box_gold_box(frame, rect)

    assert not is_gold_boxed
    assert metrics["gold_pixel_ratio"] == 0.0
