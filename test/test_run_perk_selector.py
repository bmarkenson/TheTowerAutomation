import json

import numpy as np
import pytest

from core.run_perk_selector import (
    PerkChoice,
    PerkChoicePanel,
    RunScopedPerkSelector,
    build_strict_perk_whitelist,
    canonical_perk_family,
    inspect_perk_choice_panel,
)


def _frame():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_completed_record_builds_semantic_whitelist_without_level_values():
    texts = [
        "x2.19 all coins bonuses",
        "x1.81 Damage",
        "Boss health -73.5%, but boss speed +50%",
        "x2.19 Cash Bonus",
        "Land Mine Damage x21.88",
        "Free upgrade chance for all +31.25%",
        "Golden tower bonus x1.5",
        "Chain lightning damage x2",
        "x2.50 max health",
        "Spotlight damage bonus x1.5",
        "x5.94 Health Regen",
        "Defense percent +25.00%",
        "+1 wave on death wave",
        "Chrono Field Duration +5s",
        "4 more smart missiles",
        "Perk wave requirement -75.00%",
        "Extra set of inner mines",
        "Swamp radius x1.5",
        "Black Hole duration +12.0s",
        "Orbs +2",
        "Increase max game speed by +1.25",
        "tower health regen x8.80, but tower max health -60%",
    ]
    record = {"perks": {"selected": [{"display_text": text} for text in texts]}}

    families = build_strict_perk_whitelist(record)

    assert len(families) == 22
    assert canonical_perk_family("x1.44 Damage") == "damage"
    assert canonical_perk_family("x1.50 max health") == "max_health"
    assert canonical_perk_family("Interest x1.88") is None


def test_whitelist_refuses_a_recorded_perk_it_cannot_identify():
    record = {"perks": {"selected": [{"display_text": "Interest x1.88"}]}}

    with pytest.raises(ValueError, match="unrecognized recorded Perks"):
        build_strict_perk_whitelist(record)


def test_choice_reader_uses_fixed_prompt_rows_and_confidence():
    texts = iter(
        (
            ("Perk wave requirement -25.00%", 94.0),
            ("x1.44 all coins bonuses", 93.6),
            ("4 more smart missiles", 88.4),
            ("Orbs +1", 95.0),
        )
    )

    panel = inspect_perk_choice_panel(
        _frame(),
        detector=lambda _frame: {"state": "PERKS"},
        text_fn=lambda _crop: next(texts),
        header_text_fn=lambda _crop: ("Choose a New Perk", 95.0),
    )

    assert panel.prompt_visible is True
    assert [choice.family for choice in panel.choices] == [
        "perk_wave_requirement",
        "all_coins_bonuses",
        "smart_missiles",
        "orbs",
    ]
    assert [choice.confidence for choice in panel.choices] == [
        94.0,
        93.6,
        88.4,
        95.0,
    ]


def test_selector_taps_only_a_confirmed_whitelisted_family(tmp_path):
    state_path = tmp_path / "selector.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "strict": True,
                "allowed_families": ["all_coins_bonuses"],
            }
        ),
        encoding="utf-8",
    )
    selector = RunScopedPerkSelector(state_path, clock=lambda: 100.0)
    choice = PerkChoice(
        "x1.44 all coins bonuses",
        "all_coins_bonuses",
        93.6,
        600,
        782,
    )
    prompts = iter(
        (
            PerkChoicePanel(True, True, False, (choice,)),
            PerkChoicePanel(True, True, False, (choice,)),
            PerkChoicePanel(False, True, False, ()),
        )
    )
    taps = []
    panel = _frame()

    handled = selector.handle(
        _frame(),
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        capture_fn=lambda: panel,
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
        tap_visible_fn=lambda target, **_kwargs: taps.append(target) or True,
        new_perk_fn=lambda _frame: True,
        inspect_fn=lambda _frame: next(prompts),
        sleep_fn=lambda _seconds: None,
    )

    assert handled is True
    assert taps == [
        "navigation.open_perks",
        (540, 691),
        "buttons.close:perks",
    ]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["selections"][0]["family"] == "all_coins_bonuses"
    assert "auto" not in " ".join(str(value) for value in taps).lower()


def test_selector_leaves_non_whitelisted_prompt_unresolved(tmp_path):
    state_path = tmp_path / "selector.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "strict": True,
                "allowed_families": ["damage"],
            }
        ),
        encoding="utf-8",
    )
    selector = RunScopedPerkSelector(state_path, clock=lambda: 100.0)
    offered = PerkChoice("Interest x1.88", None, 92.0, 405, 585)
    taps = []

    handled = selector.handle(
        _frame(),
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        capture_fn=_frame,
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
        tap_visible_fn=lambda target, **_kwargs: taps.append(target) or True,
        new_perk_fn=lambda _frame: True,
        inspect_fn=lambda _frame: PerkChoicePanel(
            True, True, False, (offered,)
        ),
        sleep_fn=lambda _seconds: None,
    )

    assert handled is True
    assert taps == ["navigation.open_perks", "buttons.close:perks"]
    assert "selections" not in json.loads(state_path.read_text(encoding="utf-8"))


def test_selector_refuses_choices_when_auto_pick_is_enabled(tmp_path):
    state_path = tmp_path / "selector.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "strict": True,
                "allowed_families": ["damage"],
            }
        ),
        encoding="utf-8",
    )
    selector = RunScopedPerkSelector(state_path, clock=lambda: 100.0)
    offered = PerkChoice("x1.44 Damage", "damage", 94.0, 405, 585)
    taps = []

    handled = selector.handle(
        _frame(),
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        capture_fn=_frame,
        detector=lambda _frame: {"state": "PERKS"},
        safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
        tap_visible_fn=lambda target, **_kwargs: taps.append(target) or True,
        new_perk_fn=lambda _frame: True,
        inspect_fn=lambda _frame: PerkChoicePanel(True, True, True, (offered,)),
        sleep_fn=lambda _seconds: None,
    )

    assert handled is True
    assert taps == ["navigation.open_perks", "buttons.close:perks"]
    assert "selections" not in json.loads(state_path.read_text(encoding="utf-8"))


def test_force_proven_battle_boundary_retires_enabled_selector(tmp_path):
    state_path = tmp_path / "selector.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": True,
                "strict": True,
                "allowed_families": ["damage"],
            }
        ),
        encoding="utf-8",
    )
    selector = RunScopedPerkSelector(state_path)

    assert selector.retire("active_round_identity_changed") is True

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    assert saved["completion_reason"] == "active_round_identity_changed"
    assert selector.handle(
        _frame(),
        {"state": "RUNNING"},
        action_guard_fn=lambda: True,
        new_perk_fn=lambda _frame: True,
    ) is False
