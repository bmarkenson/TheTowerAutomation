import ast
import json
from pathlib import Path

import cv2
import pytest

from core.label_tapper import get_label_match


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (ROOT / "core", ROOT / "handlers", ROOT / "automation")


def _runtime_files():
    for directory in RUNTIME_ROOTS:
        yield from sorted(directory.rglob("*.py"))


def _resolve_clickmap(dot_path: str):
    value = json.loads((ROOT / "config" / "clickmap.json").read_text())
    for segment in dot_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def test_runtime_safe_taps_have_no_blind_bypass_keywords():
    violations = []
    for path in _runtime_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            keywords = {keyword.arg for keyword in call.keywords}
            forbidden = keywords & {"require_visible", "allow_fallback"}
            if forbidden:
                violations.append(
                    f"{path.relative_to(ROOT)}:{call.lineno}:{sorted(forbidden)}"
                )
    assert violations == []


def test_direct_safe_taps_are_template_matched_or_explicitly_verified():
    violations = []
    for path in _runtime_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id != "safe_tap":
                continue
            if not call.args:
                violations.append(f"{path.relative_to(ROOT)}:{call.lineno}:no target")
                continue
            keywords = {keyword.arg for keyword in call.keywords}
            target = call.args[0]
            if isinstance(target, (ast.Tuple, ast.List)):
                if "verification" not in keywords:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{call.lineno}:raw coordinates"
                    )
                continue
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                entry = _resolve_clickmap(target.value)
                template_backed = isinstance(entry, dict) and bool(
                    entry.get("match_template")
                )
                if not template_backed and "verification" not in keywords:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{call.lineno}:{target.value}"
                    )
    assert violations == []


def test_low_level_runtime_tap_authority_is_narrowly_allowlisted():
    allowed_importers = {
        "core/input.py",
        "core/tap_dispatcher.py",
        "handlers/ad_gem_handler.py",
    }
    violations = []
    for path in _runtime_files():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = {alias.name for alias in node.names}
            if node.module == "core.adb_utils" and "input_tap" in imported:
                if relative not in allowed_importers:
                    violations.append(f"{relative}:{node.lineno}:input_tap")
            if node.module == "core.tap_dispatcher" and imported & {
                "tap",
                "tap_now",
            }:
                if relative not in allowed_importers:
                    violations.append(f"{relative}:{node.lineno}:tap_dispatcher")
    assert violations == []


def test_reusable_frame_authority_is_limited_to_urgent_purchase_blocks():
    allowed = {
        "core/damage_adjuster.py",
        "core/level_skip_initializer.py",
    }
    violations = []
    for path in _runtime_files():
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            for keyword in call.keywords:
                if (
                    keyword.arg == "reuse_authority"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    and relative not in allowed
                ):
                    violations.append(f"{relative}:{call.lineno}:reuse_authority")
    assert violations == []


def test_home_target_priority_control_does_not_exist():
    assert _resolve_clickmap("navigation.home_target_priority") is None


@pytest.mark.parametrize(
    ("target", "fixture"),
    (
        ("navigation.goto_workshop_home", "home_screen_no_reward_badges_20260714.png"),
        ("navigation.goto_cards_home", "home_screen_no_reward_badges_20260714.png"),
        ("navigation.goto_modules_home", "home_screen_no_reward_badges_20260714.png"),
        ("navigation.goto_store_home", "home_screen_no_reward_badges_20260714.png"),
        ("navigation.goto_home", "workshop_farm_active_20260714.png"),
        ("navigation.target_priority", "running_menu_no_reward_badges_20260715.png"),
        (
            "navigation.distance_adjuster",
            "running_menu_no_reward_badges_20260715.png",
        ),
        ("navigation.open_perks", "ui_state_20260714/active_wave_stats.png"),
        ("navigation.open_perks", "open_perks_dynamic_progress_20260723.png"),
        ("navigation.open_perks", "open_perks_complete_20260809.png"),
        ("navigation.workshop:upgrade", "workshop_farm_active_20260714.png"),
        (
            "navigation.workshop:attack",
            "free_upgrade_locks/bounce_shot_range_unchecked_20260720.png",
        ),
        ("navigation.workshop:defense", "workshop_farm_active_20260714.png"),
        (
            "navigation.workshop:uw",
            "free_upgrade_locks/shockwave_size_visible_workshop_20260720.png",
        ),
        ("buttons.exit_battle", "running_menu_no_reward_badges_20260715.png"),
        (
            "buttons.surrender:exit_battle",
            "ui_state_20260714/active_exit_battle_dialog.png",
        ),
        (
            "buttons.go_home:exit_battle",
            "ui_state_20260714/active_exit_battle_dialog.png",
        ),
        ("buttons.damage_adjuster:decrease", "damage_adjuster_1e22_20260714.png"),
        ("buttons.damage_adjuster:increase", "damage_adjuster_1e22_20260714.png"),
        (
            "buttons.close:distance_adjuster",
            "ui_state_20260714/active_distance_adjuster.png",
        ),
        (
            "buttons.distance_adjuster:extra:decrease",
            "ui_state_20260714/active_distance_adjuster.png",
        ),
        (
            "buttons.distance_adjuster:extra:increase",
            "ui_state_20260714/active_distance_adjuster.png",
        ),
        (
            "buttons.distance_adjuster:workshop:decrease",
            "ui_state_20260714/active_distance_adjuster.png",
        ),
        (
            "buttons.distance_adjuster:workshop:increase",
            "ui_state_20260714/active_distance_adjuster.png",
        ),
        (
            "buttons.guardian:scout_inventory",
            "guild_guardian_gc_inactive_20260715.png",
        ),
        (
            "buttons.guardian:attack_inventory",
            "guild_guardian_gc_loadout_20260713.png",
        ),
        (
            "buttons.guardian:ally_inventory",
            "guild_guardian_gc_loadout_20260713.png",
        ),
    ),
)
def test_new_runtime_tap_templates_match_retained_evidence(target, fixture):
    screenshot = cv2.imread(str(ROOT / "test" / "fixtures" / fixture))
    assert screenshot is not None

    match = get_label_match(target, screenshot=screenshot, return_meta=True)

    assert match["match_score"] >= _resolve_clickmap(target)["match_threshold"]


def test_target_priority_template_rejects_home_screen():
    screenshot = cv2.imread(
        str(ROOT / "test" / "fixtures" / "home_screen_no_reward_badges_20260714.png")
    )
    assert screenshot is not None

    with pytest.raises(ValueError, match="failed threshold"):
        get_label_match("navigation.target_priority", screenshot=screenshot)


@pytest.mark.parametrize(
    "fixture",
    (
        "home_screen_no_reward_badges_20260714.png",
        "ui_state_20260714/no_battle_perks_configuration_20260719.png",
    ),
)
def test_open_perks_template_rejects_non_battle_screens(fixture):
    screenshot = cv2.imread(str(ROOT / "test" / "fixtures" / fixture))
    assert screenshot is not None

    with pytest.raises(ValueError, match="failed threshold"):
        get_label_match("navigation.open_perks", screenshot=screenshot)
