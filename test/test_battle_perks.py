import cv2
import numpy as np

from core.battle_perks import ocr_selected_perks


def _color(hue: int) -> tuple[int, int, int]:
    hsv = np.uint8([[[hue, 220, 150]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return tuple(int(value) for value in bgr)


def _frame(colors: list[str]) -> np.ndarray:
    image = np.zeros((1920, 1080, 3), dtype=np.uint8)
    hues = {"blue": 121, "green": 57, "purple": 142}
    top = 435
    for color in colors:
        cv2.rectangle(
            image,
            (107, top),
            (972, top + 163),
            _color(hues[color]),
            thickness=-1,
        )
        top += 179
    return image


def test_order_color_and_instance_semantics_survive_overlapping_pages():
    recognized = iter(
        [
            ("Boss health -73.5%, but boss speec +50%", 94.0),
            ("Defense percent +25.00%", 93.0),
            ("Chain lightning damage x2", 95.0),
            ("Chain lightning damage x2", 96.0),
            ("x2.19 all coins bonuses", 92.0),
        ]
    )
    perks = ocr_selected_perks(
        [_frame(["purple", "blue", "green"]), _frame(["green", "blue"])],
        source_complete=True,
        source_reason="edge_reached",
        text_fn=lambda _crop: next(recognized),
    )

    assert perks["quality"]["valid"]
    assert perks["order_semantics"] == "latest_selected_first"
    assert [perk["display_text"] for perk in perks["selected"]] == [
        "Boss health -73.5%, but boss speed +50%",
        "Defense percent +25.00%",
        "Chain lightning damage x2",
        "x2.19 all coins bonuses",
    ]
    assert [perk["latest_selection_rank"] for perk in perks["selected"]] == [1, 2, 3, 4]
    assert [perk["instance_model"] for perk in perks["selected"]] == [
        "single_instance",
        "leveled",
        "single_instance",
        "leveled",
    ]
    assert perks["selected"][2]["confidence"] == 96.0
    assert len(perks["selected"][2]["observations"]) == 2


def test_known_font_artifacts_are_normalized_without_losing_raw_ocr():
    recognized = iter(
        [
            ("+] wave on death wave", 95.0),
            ("Perk wave requirement -/75.00%", 87.0),
        ]
    )
    perks = ocr_selected_perks(
        [_frame(["green", "blue"])],
        source_complete=True,
        source_reason="edge_reached",
        text_fn=lambda _crop: next(recognized),
    )

    assert [perk["display_text"] for perk in perks["selected"]] == [
        "+1 wave on death wave",
        "Perk wave requirement -75.00%",
    ]
    assert perks["selected"][0]["text_raw"] == "+] wave on death wave"


def test_incomplete_or_low_confidence_perks_require_source_evidence():
    perks = ocr_selected_perks(
        [_frame(["blue"])],
        source_complete=False,
        source_reason="max_swipes_exceeded",
        text_fn=lambda _crop: ("Defense percent +25.00%", 70.0),
    )

    assert not perks["quality"]["valid"]
    assert perks["quality"]["retain_source_images"]
    assert perks["quality"]["low_confidence_perks"]
    assert "max_swipes_exceeded" in perks["quality"]["warnings"][0]
