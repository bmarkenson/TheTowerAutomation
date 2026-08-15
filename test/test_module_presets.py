from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from core.module_presets import (
    ModulePresetConflictError,
    ModulePresetError,
    ModulePresetStore,
)
from core.strategy_authoring import legacy_farm_source_to_strategy_source
from core.strategy_profiles import StrategyProfileStore, load_published_strategy_plan


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "config" / "strategies"
LOADOUTS = ROOT / "config" / "loadouts"


def _yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _farm_modules() -> dict[str, str]:
    return copy.deepcopy(
        _yaml(LOADOUTS / "modules.yaml")["presets"]["farm_standard"]
    )


def _source(identifier: str) -> dict:
    source = legacy_farm_source_to_strategy_source(
        _yaml(STRATEGIES / "farm_t18.source.yaml"),
        display_name="Custom Module Strategy",
    )
    source.update(
        id=identifier,
        display_name="Custom Module Strategy",
        version=1,
    )
    return source


def test_bundled_catalog_exposes_exact_normalized_eight_slot_details(tmp_path):
    catalog = ModulePresetStore(tmp_path / "custom").catalog()

    assert catalog["id"] == "module_presets"
    assert catalog["errors"] == []
    assert [item["id"] for item in catalog["items"]] == [
        "farm_standard",
        "tournament_standard",
    ]
    farm = catalog["items"][0]
    assert farm == {
        "id": "farm_standard",
        "display_name": "Farm Standard",
        "origin": "bundled",
        "editable": False,
        "can_create_variant": True,
        "definition": _farm_modules(),
        "slots": [
            {
                "key": "cannon_assist",
                "display_name": "Cannon Assist",
                "family": "cannon",
                "role": "assist",
                "module": "Being Annihilator",
            },
            {
                "key": "cannon_primary",
                "display_name": "Cannon Primary",
                "family": "cannon",
                "role": "primary",
                "module": "Amplifying Strike",
            },
            {
                "key": "generator_primary",
                "display_name": "Generator Primary",
                "family": "generator",
                "role": "primary",
                "module": "Black Hole Digestor",
            },
            {
                "key": "generator_assist",
                "display_name": "Generator Assist",
                "family": "generator",
                "role": "assist",
                "module": "Singularity Harness",
            },
            {
                "key": "armor_assist",
                "display_name": "Armor Assist",
                "family": "armor",
                "role": "assist",
                "module": "Anti-Cube Portal",
            },
            {
                "key": "armor_primary",
                "display_name": "Armor Primary",
                "family": "armor",
                "role": "primary",
                "module": "Orbital Augment",
            },
            {
                "key": "core_primary",
                "display_name": "Core Primary",
                "family": "core",
                "role": "primary",
                "module": "Multiverse Nexus",
            },
            {
                "key": "core_assist",
                "display_name": "Core Assist",
                "family": "core",
                "role": "assist",
                "module": "Dimension Core",
            },
        ],
    }


def test_create_from_bundled_and_local_reopens_deterministically(tmp_path):
    profile_store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        module_preset_directory=tmp_path / "module-presets",
    )
    first = profile_store.create_module_preset(
        "farm_variant",
        "Farm Variant",
        {"preset": "farm_standard"},
    )
    local = _farm_modules()
    local["generator_primary"] = "Project Funding"
    second = profile_store.create_module_preset(
        "project_variant",
        "Project Variant",
        {"local": local},
    )

    assert first["origin"] == second["origin"] == "custom"
    assert first["definition"] == _farm_modules()
    assert second["definition"] == local
    first_snapshot = profile_store.module_preset_store.catalog()
    reopened = ModulePresetStore(tmp_path / "module-presets").catalog()
    assert reopened == first_snapshot
    assert [item["id"] for item in reopened["items"]] == [
        "farm_standard",
        "tournament_standard",
        "farm_variant",
        "project_variant",
    ]


def test_custom_module_preset_allows_repeated_empty_primary_and_assist_slots(
    tmp_path,
):
    store = ModulePresetStore(tmp_path / "custom")
    definition = _farm_modules()
    definition["cannon_primary"] = None
    definition["cannon_assist"] = "EMPTY"

    created = store.create("empty_cannon", "Empty Cannon", definition)

    assert created["definition"]["cannon_primary"] == "empty"
    assert created["definition"]["cannon_assist"] == "empty"
    reopened = ModulePresetStore(tmp_path / "custom").catalog()
    custom = next(item for item in reopened["items"] if item["id"] == "empty_cannon")
    assert custom["definition"] == created["definition"]


@pytest.mark.parametrize(
    "identifier",
    (
        "ab",
        "Farm_Variant",
        "farm-variant",
        "1farm_variant",
        "farm variant",
        "a" * 49,
    ),
)
def test_invalid_ids_are_rejected_without_a_file(tmp_path, identifier):
    store = ModulePresetStore(tmp_path / "custom")

    with pytest.raises(ModulePresetError) as error:
        store.create(identifier, "Invalid", _farm_modules())

    assert error.value.code == "invalid_module_preset_id"
    assert not list((tmp_path / "custom").glob("*.module-preset.yaml"))


def test_bundled_and_custom_id_collisions_are_immutable(tmp_path):
    store = ModulePresetStore(tmp_path / "custom")

    with pytest.raises(ModulePresetConflictError) as bundled:
        store.create("farm_standard", "Shadow", _farm_modules())
    assert bundled.value.code == "bundled_module_preset_collision"

    store.create("farm_variant", "Farm Variant", _farm_modules())
    exact = (tmp_path / "custom" / "farm_variant.module-preset.yaml").read_bytes()
    with pytest.raises(ModulePresetConflictError) as custom:
        store.create("farm_variant", "Replacement", _farm_modules())
    assert custom.value.code == "module_preset_id_collision"
    assert (tmp_path / "custom" / "farm_variant.module-preset.yaml").read_bytes() == exact


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda value: value.update(cannon_primary="Unknown Module"),
            "unknown Ancestral module",
        ),
        (
            lambda value: value.update(cannon_primary="Project Funding"),
            "not cannon",
        ),
        (
            lambda value: value.pop("core_assist"),
            "every equipped slot",
        ),
        (
            lambda value: value.update(extra_slot="Dimension Core"),
            "every equipped slot",
        ),
        (
            lambda value: value.update(cannon_primary=value["cannon_assist"]),
            "cannot repeat",
        ),
    ),
)
def test_invalid_module_definitions_never_publish(tmp_path, mutate, message):
    store = ModulePresetStore(tmp_path / "custom")
    definition = _farm_modules()
    mutate(definition)

    with pytest.raises(ModulePresetError, match=message) as error:
        store.create("invalid_modules", "Invalid Modules", definition)

    assert error.value.code == "invalid_module_preset_definition"
    assert not (tmp_path / "custom" / "invalid_modules.module-preset.yaml").exists()


@pytest.mark.parametrize("transition", ("after_stage_fsync", "after_final_link"))
def test_handled_atomic_failure_rolls_back_and_reopen_is_clean(
    tmp_path,
    transition,
):
    def fail(selected: str) -> None:
        if selected == transition:
            raise RuntimeError("injected write failure")

    directory = tmp_path / "custom"
    store = ModulePresetStore(directory, fault_hook=fail)
    with pytest.raises(ModulePresetError, match="injected write failure"):
        store.create("atomic_variant", "Atomic Variant", _farm_modules())

    assert not (directory / "atomic_variant.module-preset.yaml").exists()
    assert not (directory / ".atomic_variant.module-preset.stage.yaml").exists()
    reopened = ModulePresetStore(directory).catalog()
    assert reopened["errors"] == []
    assert [item["id"] for item in reopened["items"]] == [
        "farm_standard",
        "tournament_standard",
    ]


@pytest.mark.parametrize(
    ("transition", "retained"),
    (("after_stage_fsync", False), ("after_final_link", True)),
)
def test_crash_stage_reopens_to_one_deterministic_catalog_state(
    tmp_path,
    transition,
    retained,
):
    class SimulatedCrash(BaseException):
        pass

    def crash(selected: str) -> None:
        if selected == transition:
            raise SimulatedCrash

    directory = tmp_path / "custom"
    with pytest.raises(SimulatedCrash):
        ModulePresetStore(directory, fault_hook=crash).create(
            "crash_variant",
            "Crash Variant",
            _farm_modules(),
        )

    reopened = ModulePresetStore(directory).catalog()
    assert reopened["errors"] == []
    assert (
        "crash_variant" in [item["id"] for item in reopened["items"]]
    ) is retained
    assert not list(directory.glob(".*.stage.yaml"))


def test_concurrent_same_id_creation_has_one_winner_and_no_partial_file(tmp_path):
    directory = tmp_path / "custom"

    def create(name: str) -> str:
        try:
            ModulePresetStore(directory).create(
                "concurrent_variant",
                name,
                _farm_modules(),
            )
            return "created"
        except ModulePresetConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("First", "Second")))

    assert sorted(results) == ["conflict", "created"]
    catalog = ModulePresetStore(directory).catalog()
    custom = [item for item in catalog["items"] if item["origin"] == "custom"]
    assert len(custom) == 1
    assert custom[0]["id"] == "concurrent_variant"
    assert not list(directory.glob(".*.stage.yaml"))


def test_symlink_and_bundled_shadow_are_excluded_without_following(tmp_path):
    directory = tmp_path / "custom"
    directory.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("not: a preset\n", encoding="utf-8")
    (directory / "linked.module-preset.yaml").symlink_to(outside)
    shadow = directory / "farm_standard.module-preset.yaml"
    shadow.write_text("shadow: true\n", encoding="utf-8")

    catalog = ModulePresetStore(directory).catalog()

    assert [item["id"] for item in catalog["items"]] == [
        "farm_standard",
        "tournament_standard",
    ]
    assert {error["code"] for error in catalog["errors"]} == {
        "invalid_custom_module_preset",
        "bundled_module_preset_collision",
    }
    assert outside.read_text(encoding="utf-8") == "not: a preset\n"


def test_custom_preset_resolves_for_a_direct_strategy_publication(tmp_path):
    store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        module_preset_directory=tmp_path / "module-presets",
    )
    store.create_module_preset(
        "direct_modules",
        "Direct Modules",
        {"preset": "farm_standard"},
    )
    source = _source("direct_custom_modules")
    source["settings"]["modules"] = {
        "policy": "enforce",
        "value": {"preset": "direct_modules"},
    }

    validated = store.validate_authoring_strategy(source)
    published = store.publish_authoring_strategy(source)

    for result in (validated, published):
        snapshot = result["resolution"]["settings"]["modules"][
            "definition_snapshot"
        ]
        assert snapshot["preset"] == "direct_modules"
        assert snapshot["definition"] == _farm_modules()
    assert published["published"] is True
    assert published["profile"]["id"] == "direct_custom_modules"


def test_custom_preset_base_strategy_history_and_restore_are_self_contained(
    tmp_path,
):
    profiles = tmp_path / "profiles"
    presets = tmp_path / "module-presets"
    store = StrategyProfileStore(
        profile_directory=profiles,
        module_preset_directory=presets,
    )
    custom = store.create_module_preset(
        "durable_modules",
        "Durable Modules",
        {"preset": "farm_standard"},
    )
    base = store.publish_base(
        {
            "id": "module_base",
            "display_name": "Module Base",
            "family": "farm",
            "settings": {
                "modules": {
                    "policy": "enforce",
                    "value": {"preset": custom["id"]},
                }
            },
        }
    )
    source = _source("custom_module_history")
    source["base"] = {"id": "module_base", "revision": 1}
    source["settings"].pop("modules")
    first = store.publish(source)
    first_revision = (
        profiles / "history" / "custom_module_history.strategy.1.yaml"
    )
    exact_first = first_revision.read_bytes()
    changed = copy.deepcopy(source)
    changed["settings"]["damage_slider"] = {
        "policy": "enforce",
        "value": "1e-18",
    }
    second = store.publish(
        changed,
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )

    for path in presets.iterdir():
        path.unlink()
    presets.rmdir()
    reopened = StrategyProfileStore(
        profile_directory=profiles,
        module_preset_directory=presets,
    )
    base_catalog = reopened.base_store.catalog()
    lineage = reopened.history_catalog("custom_module_history")["lineages"][0]
    selected = lineage["revisions"][1]
    preview = reopened.compare_strategy_revision(
        "custom_module_history",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=second["profile"]["source_fingerprint"],
        require_optimistic_state=True,
    )
    restored = reopened.publish_restore_strategy(
        "custom_module_history",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=second["profile"]["source_fingerprint"],
        reviewed_restore_fingerprint=preview["reviewed_restore_fingerprint"],
    )

    assert base_catalog["errors"] == []
    assert base_catalog["items"][0]["resolution"] == base["resolution"]
    assert all(item["current_validation_valid"] for item in lineage["revisions"])
    assert preview["valid"] is True
    assert restored["profile"]["version"] == 3
    assert first_revision.read_bytes() == exact_first
    assert load_published_strategy_plan("custom_module_history", profiles) is not None
    retained = reopened.history_revision("custom_module_history", 1)
    module_snapshot = retained["resolution"]["settings"]["modules"][
        "definition_snapshot"
    ]
    assert module_snapshot["preset"] == "durable_modules"
    assert module_snapshot["definition"] == _farm_modules()
