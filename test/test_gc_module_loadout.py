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
    _equip_inventory_module,
    _filter_option_visible,
    _find_inventory_detail,
    _inventory_candidates,
    _role_prompt_visible,
    _scroll_inventory_to_top,
    _detail_ready,
    _set_module_rarity_filter,
    ensure_gc_module_loadout,
    evaluate_gc_module_loadout,
    normalize_gc_module_requirements,
)
from core.clickmap_access import get_click, get_swipe, resolve_dot_path
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
        temporary_equip_fn=lambda *_args: pytest.fail("must not use temporary"),
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


def test_module_correction_preserves_a_swap_cycle_through_temporary_module():
    module_frame = np.full((1920, 1080, 3), 32, dtype=np.uint8)
    actual = dict(GC_MODULES)
    actual["cannon_assist"] = "Amplifying Strike"
    actual["cannon_primary"] = "Being Annihilator"
    actions = []
    evaluations = []

    def evaluate(_frame, _requirements, *, catalog):
        evaluations.append(dict(actual))
        return _evidence(actual)

    def temporary_equip(slot, excluded_names):
        actions.append(
            (
                "temporary",
                slot.slot_key,
                slot.actual,
                frozenset(excluded_names),
            )
        )
        actual[slot.slot_key] = "Shrink Ray"
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
        temporary_equip_fn=temporary_equip,
    )

    assert result.valid
    assert actions == [
        (
            "temporary",
            "cannon_assist",
            "Amplifying Strike",
            frozenset(GC_MODULES.values()),
        ),
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
            temporary_equip_fn=lambda *_args: pytest.fail(
                "must not use temporary"
            ),
        )
    assert not GcModuleLoadoutEvidence(tuple(uncertain)).has_authoritative_mismatch


@pytest.mark.parametrize("role", ("primary", "assist"))
def test_module_replacement_always_accepts_level_transfer(role):
    detail = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    role_prompt = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    transfer_prompt = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    overview = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    captures = iter((role_prompt, transfer_prompt))
    taps = []
    slot = GcModuleSlotEvidence(
        slot_key=f"cannon_{role}",
        family="cannon",
        role=role,
        expected="Being Annihilator",
        actual="Project Funding",
        match_status="matched",
        valid=False,
        confidence=1.0,
        margin=1.0,
        green_fraction=1.0,
    )

    def safe_tap(target, **kwargs):
        verification = kwargs["verification"]
        point = get_click(target)
        assert point is not None
        assert verification.authorizes(point)
        taps.append((target, verification.description))
        return True

    def wait_for(_predicate, *, reason, **_kwargs):
        if reason == "Primary/Assist module role prompt":
            return role_prompt
        assert reason == (
            "settled Ancestral Modules overview after accepted level transfer"
        )
        return overview

    with (
        patch("core.gc_module_loadout._find_inventory_detail", return_value=detail),
        patch("core.gc_module_loadout._detail_for", return_value=True),
        patch(
            "core.gc_module_loadout._role_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 20,
        ),
        patch(
            "core.gc_module_loadout._transfer_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 30,
        ),
        patch(
            "core.gc_module_loadout._overview_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 40,
        ),
        patch("core.gc_module_loadout._capture_modules", side_effect=captures),
        patch("core.gc_module_loadout._wait_for", side_effect=wait_for),
    ):
        result = _equip_inventory_module(
            slot,
            capture_fn=lambda: pytest.fail("capture is wrapped"),
            detector=lambda _frame: {"state": "MODULES"},
            safe_tap_fn=safe_tap,
            swipe_fn=lambda _label: True,
            sleep_fn=lambda _seconds: None,
            catalog=load_module_icon_catalog(),
        )

    assert result is overview
    assert taps == [
        (
            "buttons.module:detail_equip_toggle",
            "module_detail:equip:Being Annihilator",
        ),
        (f"buttons.module:select_{role}", f"module_role:{role}"),
        (
            "buttons.module:accept_level_transfer",
            "module_level_transfer:accept",
        ),
    ]


def test_module_replacement_fails_when_level_transfer_cannot_be_accepted():
    detail = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    role_prompt = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    transfer_prompt = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    captures = iter((role_prompt, transfer_prompt))
    slot = GcModuleSlotEvidence(
        slot_key="cannon_primary",
        family="cannon",
        role="primary",
        expected="Being Annihilator",
        actual="Project Funding",
        match_status="matched",
        valid=False,
        confidence=1.0,
        margin=1.0,
        green_fraction=1.0,
    )

    def safe_tap(target, **_kwargs):
        return target != "buttons.module:accept_level_transfer"

    with (
        patch("core.gc_module_loadout._find_inventory_detail", return_value=detail),
        patch("core.gc_module_loadout._detail_for", return_value=True),
        patch(
            "core.gc_module_loadout._role_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 20,
        ),
        patch(
            "core.gc_module_loadout._transfer_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 30,
        ),
        patch("core.gc_module_loadout._capture_modules", side_effect=captures),
        patch(
            "core.gc_module_loadout._wait_for",
            return_value=role_prompt,
        ),
    ):
        with pytest.raises(
            ModuleLoadoutCorrectionError,
            match="failed to accept module level transfer",
        ):
            _equip_inventory_module(
                slot,
                capture_fn=lambda: pytest.fail("capture is wrapped"),
                detector=lambda _frame: {"state": "MODULES"},
                safe_tap_fn=safe_tap,
                swipe_fn=lambda _label: True,
                sleep_fn=lambda _seconds: None,
                catalog=load_module_icon_catalog(),
            )


def test_module_replacement_rejects_missing_level_transfer_prompt():
    detail = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    role_prompt = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    overview = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    slot = GcModuleSlotEvidence(
        slot_key="armor_primary",
        family="armor",
        role="primary",
        expected="Orbital Augment",
        actual="Anti-Cube Portal",
        match_status="matched",
        valid=False,
        confidence=1.0,
        margin=1.0,
        green_fraction=1.0,
    )

    with (
        patch("core.gc_module_loadout._find_inventory_detail", return_value=detail),
        patch("core.gc_module_loadout._detail_for", return_value=True),
        patch(
            "core.gc_module_loadout._role_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 20,
        ),
        patch("core.gc_module_loadout._transfer_prompt_visible", return_value=False),
        patch(
            "core.gc_module_loadout._overview_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 40,
        ),
        patch("core.gc_module_loadout._capture_modules", return_value=overview),
        patch("core.gc_module_loadout._wait_for", return_value=role_prompt),
    ):
        with pytest.raises(
            ModuleLoadoutCorrectionError,
            match="without offering the required level transfer",
        ):
            _equip_inventory_module(
                slot,
                capture_fn=lambda: pytest.fail("capture is wrapped"),
                detector=lambda _frame: {"state": "MODULES"},
                safe_tap_fn=lambda *_args, **_kwargs: True,
                swipe_fn=lambda _label: True,
                sleep_fn=lambda _seconds: None,
                catalog=load_module_icon_catalog(),
            )


def test_empty_slot_recovery_rejects_unexpected_level_transfer_prompt():
    detail = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    role_prompt = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    transfer_prompt = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    taps = []
    slot = GcModuleSlotEvidence(
        slot_key="armor_assist",
        family="armor",
        role="assist",
        expected="Orbital Augment",
        actual=None,
        match_status="not_ancestral",
        valid=False,
        confidence=0.0,
        margin=0.0,
        green_fraction=0.0,
    )

    with (
        patch("core.gc_module_loadout._find_inventory_detail", return_value=detail),
        patch("core.gc_module_loadout._detail_for", return_value=True),
        patch(
            "core.gc_module_loadout._role_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 20,
        ),
        patch(
            "core.gc_module_loadout._transfer_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 30,
        ),
        patch(
            "core.gc_module_loadout._capture_modules",
            return_value=transfer_prompt,
        ),
        patch("core.gc_module_loadout._wait_for", return_value=role_prompt),
    ):
        with pytest.raises(
            ModuleLoadoutCorrectionError,
            match="unexpectedly offered level transfer",
        ):
            _equip_inventory_module(
                slot,
                capture_fn=lambda: pytest.fail("capture is wrapped"),
                detector=lambda _frame: {"state": "MODULES"},
                safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
                swipe_fn=lambda _label: True,
                sleep_fn=lambda _seconds: None,
                catalog=load_module_icon_catalog(),
                require_level_transfer=False,
                allow_level_transfer=False,
            )

    assert taps == [
        "buttons.module:detail_equip_toggle",
        "buttons.module:select_assist",
    ]


def test_module_replacement_retries_a_dropped_equip_input_once():
    detail = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    role_prompt = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    transfer_prompt = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    overview = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    captures = iter((detail, transfer_prompt))
    taps = []
    slot = GcModuleSlotEvidence(
        slot_key="generator_primary",
        family="generator",
        role="primary",
        expected="Black Hole Digestor",
        actual="Project Funding",
        match_status="matched",
        valid=False,
        confidence=1.0,
        margin=1.0,
        green_fraction=1.0,
    )
    prompt_waits = 0

    def safe_tap(target, **_kwargs):
        taps.append(target)
        return True

    def wait_for(_predicate, *, reason, **_kwargs):
        nonlocal prompt_waits
        if reason == "Primary/Assist module role prompt":
            prompt_waits += 1
            if prompt_waits == 1:
                raise ModuleLoadoutCorrectionError(
                    "timed out waiting for Primary/Assist module role prompt"
                )
            return role_prompt
        assert reason == (
            "settled Ancestral Modules overview after accepted level transfer"
        )
        return overview

    with (
        patch("core.gc_module_loadout._find_inventory_detail", return_value=detail),
        patch("core.gc_module_loadout._detail_for", return_value=True),
        patch(
            "core.gc_module_loadout._role_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 20,
        ),
        patch(
            "core.gc_module_loadout._transfer_prompt_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 30,
        ),
        patch(
            "core.gc_module_loadout._overview_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) == 40,
        ),
        patch("core.gc_module_loadout._capture_modules", side_effect=captures),
        patch("core.gc_module_loadout._wait_for", side_effect=wait_for),
    ):
        result = _equip_inventory_module(
            slot,
            capture_fn=lambda: pytest.fail("capture is wrapped"),
            detector=lambda _frame: {"state": "MODULES"},
            safe_tap_fn=safe_tap,
            swipe_fn=lambda _label: True,
            sleep_fn=lambda _seconds: None,
            catalog=load_module_icon_catalog(),
        )

    assert result is overview
    assert taps == [
        "buttons.module:detail_equip_toggle",
        "buttons.module:detail_equip_toggle",
        "buttons.module:select_primary",
        "buttons.module:accept_level_transfer",
    ]


def test_module_level_transfer_clickmap_has_only_the_accept_action():
    assert get_click("buttons.module:accept_level_transfer") == (730, 1125)
    assert resolve_dot_path("buttons.module:decline_level_transfer") is None


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


def test_inventory_candidates_require_authoritative_target_identity():
    frame = _load("gc_modules_overview.png")
    catalog = load_module_icon_catalog()

    # Both desired cannon/armor modules are equipped in this fixture. The
    # inventory contains other same-family Ancestral modules, but none may be
    # opened merely because it has a weak positive score for the absent target.
    assert _inventory_candidates(frame, "Being Annihilator", catalog) == []
    assert _inventory_candidates(frame, "Orbital Augment", catalog) == []

    funding = _inventory_candidates(frame, "Project Funding", catalog)
    assert funding
    score, margin, center = funding[0]
    assert center == (741, 1090)
    assert score >= catalog.inventory_minimum_confidence
    assert margin >= catalog.inventory_minimum_margin


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
            return_value=[(0.9, 0.4, (145, 1090))],
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
                level=1,
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


def test_rarity_filter_actions_use_each_fresh_panel_frame():
    initial = np.full((1920, 1080, 3), 10, dtype=np.uint8)
    panel = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    cleared = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    selected = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    closed = np.full((1920, 1080, 3), 50, dtype=np.uint8)
    taps = []

    def tap(target, **kwargs):
        taps.append((target, kwargs["verification"].screenshot))
        return True

    with (
        patch(
            "core.gc_module_loadout._capture_modules",
            return_value=initial,
        ),
        patch(
            "core.gc_module_loadout._wait_for",
            side_effect=(panel, cleared, selected, closed),
        ),
        patch(
            "core.gc_module_loadout._filter_panel_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) in {20, 30, 40},
        ),
        patch("core.gc_module_loadout._filter_option_visible", return_value=True),
        patch("core.gc_module_loadout._filter_label", return_value="ANCESTRAL"),
    ):
        result = _set_module_rarity_filter(
            "ancestral",
            capture_fn=lambda: pytest.fail("capture is wrapped"),
            detector=lambda _frame: {"state": "MODULES"},
            safe_tap_fn=tap,
            sleep_fn=lambda _seconds: None,
        )

    assert result is closed
    assert [target for target, _frame in taps] == [
        "buttons.module:rarity_filter",
        "buttons.module:rarity_none",
        "buttons.module:rarity_ancestral",
        "buttons.module:rarity_filter",
    ]
    assert all(
        actual is expected
        for (_target, actual), expected in zip(
            taps,
            (initial, panel, cleared, selected),
        )
    )


def test_rarity_filter_recovers_from_an_already_open_panel():
    panel = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    cleared = np.full((1920, 1080, 3), 30, dtype=np.uint8)
    selected = np.full((1920, 1080, 3), 40, dtype=np.uint8)
    closed = np.full((1920, 1080, 3), 50, dtype=np.uint8)
    taps = []

    with (
        patch("core.gc_module_loadout._capture_modules", return_value=panel),
        patch(
            "core.gc_module_loadout._wait_for",
            side_effect=(panel, cleared, selected, closed),
        ),
        patch(
            "core.gc_module_loadout._filter_panel_visible",
            side_effect=lambda frame: int(frame[0, 0, 0]) != 50,
        ),
        patch("core.gc_module_loadout._filter_option_visible", return_value=True),
        patch("core.gc_module_loadout._filter_label", return_value="ANCESTRAL"),
    ):
        result = _set_module_rarity_filter(
            "ancestral",
            capture_fn=lambda: pytest.fail("capture is wrapped"),
            detector=lambda _frame: {"state": "MODULES"},
            safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
            sleep_fn=lambda _seconds: None,
        )

    assert result is closed
    assert taps == [
        "buttons.module:rarity_none",
        "buttons.module:rarity_ancestral",
        "buttons.module:rarity_filter",
    ]


def test_ancestral_filter_ocr_excludes_the_adjacent_mythic_row():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    crops = []

    def read_text(crop, *, psm):
        crops.append((crop.shape, psm))
        return "Ancestral", 95.0

    with patch("core.gc_module_loadout.ocr_text_and_conf", side_effect=read_text):
        assert _filter_option_visible(frame, "ANCESTRAL")

    assert crops == [((115, 370, 3), 7)]


def test_role_prompt_ocr_falls_back_to_sparse_text_layout():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    reads = iter(
        (
            ("Equip a module", 95.0),
            ("Primary Assist", 95.0),
        )
    )

    with patch("core.gc_module_loadout.ocr_text_and_conf", side_effect=reads):
        assert _role_prompt_visible(frame)


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
        level=None,
    )
    complete = ModuleDetailEvidence(
        name="Project Funding",
        rarity="ANCESTRAL",
        equipped="",
        action="EQUIP",
        level=1,
    )
    with patch("core.gc_module_loadout._read_detail", return_value=partial):
        assert not _detail_ready(incomplete)
    with patch("core.gc_module_loadout._read_detail", return_value=complete):
        assert _detail_ready(incomplete)
