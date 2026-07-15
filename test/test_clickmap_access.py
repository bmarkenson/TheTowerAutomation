from unittest.mock import patch

from core.clickmap_access import get_click, get_explicit_tap, resolve_dot_path
from core.input import safe_tap


def test_legacy_get_click_preserves_direct_match_region_center():
    entry = resolve_dot_path("buttons.battle:home")
    assert entry is not None
    assert "match_region" in entry
    assert "tap" not in entry

    assert get_click("buttons.battle:home") == (538, 1559)
    assert get_explicit_tap("buttons.battle:home") is None


def test_broad_region_ref_never_becomes_a_click_center():
    entry = resolve_dot_path("upgrades.utility.left.EHLS")
    assert entry is not None
    assert entry["region_ref"] == "upgrades_left"

    assert get_click("upgrades.utility.left.EHLS") is None


def test_runtime_blind_tap_rejects_derived_region_center():
    with patch("core.input._dispatch_tap") as dispatch:
        assert not safe_tap("buttons.battle:home", require_visible=False)

    dispatch.assert_not_called()


def test_blind_navigation_targets_have_explicit_tap_geometry():
    assert get_click("navigation.goto_attack") == (136, 1864)
    assert get_click("navigation.goto_defense") == (406, 1868)
    assert get_click("navigation.goto_utility") == (670, 1867)
    assert get_click("navigation.goto_uw") == (941, 1871)
    assert get_click("navigation.goto_home_store") == (80, 1830)
