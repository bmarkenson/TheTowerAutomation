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


def test_safe_tap_separates_operator_summary_from_coordinate_detail(
    tmp_path,
    monkeypatch,
):
    action_log = tmp_path / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(action_log))

    with patch("core.input._dispatch_tap") as dispatch:
        assert safe_tap(
            (10, 20),
            require_visible=False,
            dispatch="queue",
            log_label="test_target",
        )

    dispatch.assert_called_once_with(10, 20, label="test_target", dispatch="queue")
    lines = action_log.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("] Tap queued: Test target")
    assert lines[1].endswith(
        "] TAP_SAFE now=False label=test_target at (10,20) vis=False"
    )


def test_blind_navigation_targets_have_explicit_tap_geometry():
    assert get_click("navigation.goto_attack") == (136, 1864)
    assert get_click("navigation.goto_defense") == (406, 1868)
    assert get_click("navigation.goto_utility") == (670, 1867)
    assert get_click("navigation.goto_uw") == (941, 1871)
    assert get_click("navigation.goto_home_store") == (80, 1830)
    assert get_click("navigation.goto_modules_home") == (630, 1830)


def test_module_loadout_inspection_targets_have_explicit_tap_geometry():
    assert get_click("buttons.module:equipped_outer_left_top") == (115, 407)
    assert get_click("buttons.module:equipped_inner_left_top") == (307, 407)
    assert get_click("buttons.module:equipped_inner_right_top") == (773, 407)
    assert get_click("buttons.module:equipped_outer_right_top") == (964, 407)
    assert get_click("buttons.module:equipped_outer_left_bottom") == (115, 663)
    assert get_click("buttons.module:equipped_inner_left_bottom") == (307, 663)
    assert get_click("buttons.module:equipped_inner_right_bottom") == (773, 663)
    assert get_click("buttons.module:equipped_outer_right_bottom") == (964, 663)
    assert get_click("buttons.close:module_detail") == (929, 223)
