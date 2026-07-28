from core.clickmap_access import get_swipe


def test_card_inventory_swipes_stay_inside_scrollable_inventory():
    assert get_swipe("gesture_targets.goto_top:cards_inventory") == {
        "x1": 540,
        "y1": 1100,
        "x2": 540,
        "y2": 1650,
        "duration_ms": 300,
    }
    assert get_swipe("gesture_targets.goto_next:cards_inventory") == {
        "x1": 540,
        "y1": 1600,
        "x2": 540,
        "y2": 1300,
        "duration_ms": 600,
    }
