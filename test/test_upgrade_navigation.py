from unittest.mock import patch

from core.upgrade_navigation import swipe_upgrade_menu


def test_upgrade_swipe_records_action_before_dispatch():
    events = []

    with (
        patch(
            "core.upgrade_navigation.log_action",
            side_effect=lambda *args, **kwargs: events.append(
                ("action", args, kwargs)
            ),
        ),
        patch(
            "core.upgrade_navigation.input_swipe",
            side_effect=lambda *args, **kwargs: events.append(
                ("swipe", args, kwargs)
            ),
        ),
    ):
        swipe_upgrade_menu("towards_top", "short")

    assert [kind for kind, _args, _kwargs in events] == ["action", "swipe"]
    assert events[0][1] == ("Swipe requested: Upgrade menu toward the top",)
    assert "direction=towards_top span=short" in events[0][2]["detail"]
    assert events[1][1] == (531, 1433, 531, 1538, 350)
