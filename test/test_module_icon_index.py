from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from core.module_icon_index import (
    identify_equipped_ancestral_modules,
    load_module_icon_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "module_inventory_20260716"

GC_EXPECTED = {
    "cannon_assist": ("assist", "Being Annihilator"),
    "cannon_primary": ("primary", "Amplifying Strike"),
    "generator_primary": ("primary", "Black Hole Digestor"),
    "generator_assist": ("assist", "Singularity Harness"),
    "armor_assist": ("assist", "Anti-Cube Portal"),
    "armor_primary": ("primary", "Orbital Augment"),
    "core_primary": ("primary", "Multiverse Nexus"),
    "core_assist": ("assist", "Dimension Core"),
}


def _load(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, f"fixture is unreadable: {name}"
    return image


def _by_slot(screen: np.ndarray):
    return {
        result.slot_key: result
        for result in identify_equipped_ancestral_modules(screen)
    }


def test_ancestral_catalog_has_six_reviewed_icons_per_family():
    catalog = load_module_icon_catalog()

    assert catalog.rarity == "Ancestral"
    assert len(catalog.modules) == 24
    assert len({module.slug for module in catalog.modules}) == 24
    assert len({module.name for module in catalog.modules}) == 24
    assert Counter(module.family for module in catalog.modules) == {
        "cannon": 6,
        "generator": 6,
        "armor": 6,
        "core": 6,
    }
    for module in catalog.modules:
        image = cv2.imread(str(module.template_path))
        assert image is not None
        assert image.shape == (200, 200, 3)


def test_restored_gc_overview_identifies_all_equipped_modules_read_only():
    screen = _load("gc_modules_overview.png")
    original = screen.copy()

    matches = _by_slot(screen)

    assert np.array_equal(screen, original)
    assert set(matches) == set(GC_EXPECTED)
    for slot_key, (role, name) in GC_EXPECTED.items():
        result = matches[slot_key]
        assert result.status == "matched"
        assert result.role == role
        assert result.name == name
        assert result.slug is not None
        assert result.confidence >= 0.23
        assert result.margin >= 0.08
        assert result.green_fraction >= 0.12


def test_equipped_module_identity_tolerates_six_pixel_overview_animation():
    screen = _load("gc_modules_overview.png")
    shifted = np.zeros_like(screen)
    shifted[6:] = screen[:-6]

    matches = _by_slot(shifted)

    assert set(matches) == set(GC_EXPECTED)
    for slot_key, (_role, name) in GC_EXPECTED.items():
        result = matches[slot_key]
        assert result.status == "matched"
        assert result.name == name
        assert result.confidence >= 0.23
        assert result.margin >= 0.08


def test_same_project_funding_icon_matches_at_primary_and_assist_scales():
    primary = _by_slot(_load("project_funding_primary_overview.png"))[
        "generator_primary"
    ]
    assist = _by_slot(_load("project_funding_assist_overview.png"))[
        "generator_assist"
    ]

    assert primary.status == "matched"
    assert primary.name == "Project Funding"
    assert primary.role == "primary"
    assert assist.status == "matched"
    assert assist.name == "Project Funding"
    assert assist.role == "assist"


def test_close_competing_icons_return_ambiguous_without_authoritative_name():
    screen = _load("project_funding_primary_overview.png")
    funding = cv2.imread(
        str(
            ROOT
            / "assets"
            / "match_templates"
            / "modules"
            / "ancestral"
            / "project_funding.png"
        )
    )
    compressor = cv2.imread(
        str(
            ROOT
            / "assets"
            / "match_templates"
            / "modules"
            / "ancestral"
            / "galaxy_compressor.png"
        )
    )
    assert funding is not None and compressor is not None
    blend = np.clip(
        (funding.astype(np.float32) + compressor.astype(np.float32)) / 2.0,
        0,
        255,
    ).astype(np.uint8)
    screen[343:477, 706:840] = cv2.resize(
        blend, (134, 134), interpolation=cv2.INTER_AREA
    )

    result = _by_slot(screen)["generator_primary"]

    assert result.status == "ambiguous"
    assert result.name is None
    assert result.slug is None
    assert result.best_candidate in {"Galaxy Compressor", "Project Funding"}
    assert result.runner_up in {"Galaxy Compressor", "Project Funding"}
    assert result.margin < 0.08


def test_unreadable_icon_returns_unknown_without_authoritative_name():
    screen = _load("project_funding_primary_overview.png")
    screen[343:477, 706:840] = (20, 20, 20)

    result = _by_slot(screen)["generator_primary"]

    assert result.status == "unknown"
    assert result.name is None
    assert result.slug is None
    assert result.best_candidate is None


def test_non_green_slots_are_not_claimed_as_ancestral_modules():
    results = identify_equipped_ancestral_modules(
        np.zeros((1920, 1080, 3), dtype=np.uint8)
    )

    assert len(results) == 8
    assert {result.status for result in results} == {"not_ancestral"}
    assert all(result.name is None for result in results)
