from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.gc_module_loadout import (
    GcModuleLoadoutEvidence,
    GcModuleSlotEvidence,
    MODULE_DETAIL_SETTLE_SECONDS,
    ModuleDetailEvidence,
    ModuleLoadoutCorrectionError,
    _find_inventory_detail,
    _scroll_inventory_to_top,
    _detail_ready,
    ensure_gc_module_loadout,
    evaluate_gc_module_loadout,
    normalize_gc_module_requirements,
)
from core.clickmap_access import get_swipe
from core.module_icon_index import load_module_icon_catalog


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "module_inventory_20260716"
GC_MODULES = {
    "cannon_assist": "Being Annihilator",
    "cannon_primary": "Amplifying Strike",
    "generator_primary": "Black Hole Digestor",
    "generator_assist": "Singularity Harness",
    "armor_assist": "Anti-Cube Portal",
    "armor_primary": "Orbital Augment",
    "core_primary": "Multiverse Nexus",
    "core_assist": "Dimension Core",
}


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    return image


def test_gc_module_overview_satisfies_exact_profile_mapping():
    evidence = evaluate_gc_module_loadout(
        _load("gc_modules_overview.png"),
        GC_MODULES,
    )

    assert evidence.valid
    assert {slot.slot_key: slot.actual for slot in evidence.slots} == GC_MODULES


def test_known_wrong_generator_module_is_an_authoritative_mismatch():
    evidence = evaluate_gc_module_loadout(
        _load("project_funding_primary_overview.png"),
        GC_MODULES,
    )

    assert not evidence.valid
    mismatch = next(slot for slot in evidence.slots if not slot.valid)
    assert mismatch.slot_key == "generator_primary"
    assert mismatch.expected == "Black Hole Digestor"
    assert mismatch.actual == "Project Funding"
    assert mismatch.match_status == "matched"
    assert evidence.has_authoritative_mismatch


def test_already_correct_gc_modules_do_not_send_correction_actions():
    result = ensure_gc_module_loadout(
        GC_MODULES,
        screenshot=_load("gc_modules_overview.png"),
        detector=lambda _frame: {"state": "MODULES"},
        equip_fn=lambda _slot: pytest.fail("must not equip"),
        unequip_fn=lambda _slot: pytest.fail("must not unequip"),
    )

    assert result.valid


def test_gc_module_requirements_must_cover_each_family_role_once():
    with pytest.raises(ValueError, match="every equipped slot"):
        normalize_gc_module_requirements(
            {key: value for key, value in GC_MODULES.items() if key != "core_assist"}
        )

    wrong_family = dict(GC_MODULES)
    wrong_family["core_assist"] = "Project Funding"
    with pytest.raises(ValueError, match="not core"):
        normalize_gc_module_requirements(wrong_family)


def _evidence(actual_by_slot):
    catalog = load_module_icon_catalog()
    slots = []
    for catalog_slot in catalog.slots:
        actual = actual_by_slot[catalog_slot.key]
        status = "matched" if actual is not None else "not_ancestral"
        slots.append(
            GcModuleSlotEvidence(
                slot_key=catalog_slot.key,
                family=catalog_slot.family,
                role=catalog_slot.role,
                expected=GC_MODULES[catalog_slot.key],
                actual=actual,
                match_status=status,
                valid=actual == GC_MODULES[catalog_slot.key],
                confidence=1.0 if actual is not None else 0.0,
                margin=1.0 if actual is not None else 0.0,
                green_fraction=1.0 if actual is not None else 0.0,
            )
        )
    return GcModuleLoadoutEvidence(tuple(slots))


def test_module_correction_breaks_a_swap_cycle_then_revalidates_every_step():
    module_frame = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    actual = dict(GC_MODULES)
    actual["cannon_assist"] = "Amplifying Strike"
    actual["cannon_primary"] = "Being Annihilator"
    actions = []
    evaluations = []

    def evaluate(_frame, _requirements, *, catalog):
        evaluations.append(dict(actual))
        return _evidence(actual)

    def unequip(slot):
        actions.append(("unequip", slot.slot_key, slot.actual))
        actual[slot.slot_key] = None
        return module_frame

    def equip(slot):
        actions.append(("equip", slot.slot_key, slot.expected))
        actual[slot.slot_key] = slot.expected
        return module_frame

    result = ensure_gc_module_loadout(
        GC_MODULES,
        screenshot=module_frame,
        detector=lambda _frame: {"state": "MODULES"},
        evaluate_fn=evaluate,
        equip_fn=equip,
        unequip_fn=unequip,
    )

    assert result.valid
    assert actions == [
        ("unequip", "cannon_assist", "Amplifying Strike"),
        ("equip", "cannon_primary", "Amplifying Strike"),
        ("equip", "cannon_assist", "Being Annihilator"),
    ]
    assert len(evaluations) == 4


def test_module_correction_refuses_unknown_overview_evidence():
    module_frame = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    evidence = _evidence(dict(GC_MODULES))
    uncertain = list(evidence.slots)
    slot = uncertain[0]
    uncertain[0] = GcModuleSlotEvidence(
        **{
            **slot.__dict__,
            "actual": None,
            "match_status": "unknown",
            "valid": False,
        }
    )

    with pytest.raises(ModuleLoadoutCorrectionError, match="uncertain"):
        ensure_gc_module_loadout(
            GC_MODULES,
            screenshot=module_frame,
            detector=lambda _frame: {"state": "MODULES"},
            evaluate_fn=lambda *_args, **_kwargs: GcModuleLoadoutEvidence(
                tuple(uncertain)
            ),
            equip_fn=lambda _slot: pytest.fail("must not equip"),
            unequip_fn=lambda _slot: pytest.fail("must not unequip"),
        )
    assert not GcModuleLoadoutEvidence(tuple(uncertain)).has_authoritative_mismatch


def test_inventory_search_rewinds_to_top_before_ranking_candidates():
    end = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    top = end.copy()
    top[980:1630] = 255
    captures = iter((top, top))
    swipes = []

    result = _scroll_inventory_to_top(
        end,
        capture_fn=lambda: next(captures),
        detector=lambda _frame: {"state": "MODULES"},
        swipe_fn=lambda label: swipes.append(label) or True,
        sleep_fn=lambda _seconds: None,
    )

    assert result is top
    assert swipes == [
        "gesture_targets.goto_previous:module_inventory",
        "gesture_targets.goto_previous:module_inventory",
    ]
    assert get_swipe("gesture_targets.goto_previous:module_inventory") == {
        "x1": 540,
        "y1": 1290,
        "x2": 540,
        "y2": 1500,
        "duration_ms": 260,
    }


def test_inventory_candidate_waits_for_fresh_detail_before_ocr():
    frame = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    events = []

    with (
        patch(
            "core.gc_module_loadout._set_module_rarity_filter",
            return_value=frame,
        ),
        patch(
            "core.gc_module_loadout._scroll_inventory_to_top",
            return_value=frame,
        ),
        patch(
            "core.gc_module_loadout._inventory_candidates",
            return_value=[(0.9, (145, 1090))],
        ),
        patch(
            "core.gc_module_loadout.ancestral_green_fraction",
            return_value=1.0,
        ),
        patch(
            "core.gc_module_loadout._wait_for",
            side_effect=lambda *_args, **_kwargs: events.append("wait") or frame,
        ),
        patch(
            "core.gc_module_loadout._read_detail",
            return_value=ModuleDetailEvidence(
                name="Dimension Core",
                rarity="ANCESTRAL",
                equipped="",
                action="EQUIP",
            ),
        ),
    ):
        result = _find_inventory_detail(
            "Dimension Core",
            capture_fn=lambda: frame,
            detector=lambda _frame: {"state": "MODULES"},
            safe_tap_fn=lambda *_args, **_kwargs: events.append("tap") or True,
            swipe_fn=lambda _label: True,
            sleep_fn=lambda seconds: events.append(("sleep", seconds)),
            catalog=load_module_icon_catalog(),
        )

    assert result is frame
    assert events == [
        "tap",
        ("sleep", MODULE_DETAIL_SETTLE_SECONDS),
        "wait",
    ]


def test_module_actions_reject_incomplete_frames_and_partial_detail_renders():
    incomplete = np.zeros((1920, 1080, 3), dtype=np.uint8)
    with pytest.raises(ModuleLoadoutCorrectionError, match="incomplete"):
        ensure_gc_module_loadout(
            GC_MODULES,
            screenshot=incomplete,
            detector=lambda _frame: {"state": "MODULES"},
        )

    partial = ModuleDetailEvidence(
        name="Project Funding",
        rarity="ANCESTRAL",
        equipped="",
        action="",
    )
    complete = ModuleDetailEvidence(
        name="Project Funding",
        rarity="ANCESTRAL",
        equipped="",
        action="EQUIP",
    )
    with patch("core.gc_module_loadout._read_detail", return_value=partial):
        assert not _detail_ready(incomplete)
    with patch("core.gc_module_loadout._read_detail", return_value=complete):
        assert _detail_ready(incomplete)
