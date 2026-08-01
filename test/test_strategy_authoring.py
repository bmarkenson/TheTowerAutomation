from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

import core.strategy_profiles as strategy_profiles_module
from core.strategy_authoring import (
    FARM_SETTING_REGISTRY,
    StrategyAuthoringError,
    StrategyBaseStore,
    farm_source_from_resolution,
    legacy_farm_source_to_strategy_source,
    normalize_base_source,
    normalize_strategy_source,
    resolve_strategy_source,
    setting_registry_catalog,
)
from core.strategy_profiles import (
    StrategyProfileConflictError,
    StrategyProfileError,
    StrategyProfileStore,
    configurable_strategy_ids,
    load_published_strategy_plan,
)
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIRECTORY = ROOT / "config" / "strategies"


def _yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _bundled_authoring(name: str = "farm_t18") -> dict:
    return legacy_farm_source_to_strategy_source(
        _yaml(STRATEGY_DIRECTORY / f"{name}.source.yaml"),
        display_name=f"{name} authoring",
    )


def _custom_source(
    identifier: str,
    *,
    base: dict | None = None,
    settings: dict | None = None,
) -> dict:
    source = {
        "schema_version": 2,
        "kind": "strategy",
        "id": identifier,
        "display_name": "Farm Custom",
        "family": "farm",
        "tier": 18,
        "version": 1,
        "settings": settings or {},
    }
    if base is not None:
        source["base"] = base
    return source


def _base_snapshot(
    *,
    settings: dict | None = None,
    family: str = "farm",
    revision: int = 1,
) -> dict:
    return normalize_base_source(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": family,
            "revision": revision,
            "settings": settings or {},
        }
    )


def test_farm_setting_registry_covers_the_complete_compact_builder_contract():
    expected = {
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "free_upgrade_locks",
        "bots_preset",
        "guardian_chips",
        "auto_pick_perks",
        "perk_bans",
        "perk_auto_pick_order",
        "ultimate_weapons",
        "modules",
        "damage_slider",
        "orb_distance",
        "target_priority",
    }

    assert set(FARM_SETTING_REGISTRY) == expected
    catalog = setting_registry_catalog()
    assert {item["id"] for item in catalog} == expected
    for item in catalog:
        assert item["display_name"]
        assert item["section"]
        assert item["editor_type"]
        assert item["allowed_policies"]
        assert item["runtime_destination"]
        assert isinstance(item["dependencies"], list)
        assert isinstance(item["observation_supported"], bool)
        assert isinstance(item["repair_supported"], bool)

    assert FARM_SETTING_REGISTRY["perk_auto_pick_order"].dependencies == (
        "auto_pick_perks",
    )
    assert FARM_SETTING_REGISTRY["damage_slider"].allowed_policies == (
        "enforce",
        "observe",
        "ignore",
    )


@pytest.mark.parametrize(
    ("base_directive", "local_directive", "state", "policy", "provenance"),
    (
        (
            {"policy": "observe", "value": "1e-19"},
            None,
            "effective",
            "observe",
            "base",
        ),
        (
            None,
            {"policy": "enforce", "value": "1e-18"},
            "effective",
            "enforce",
            "local",
        ),
        (
            {"policy": "observe", "value": "1e-19"},
            {"policy": "enforce", "value": "1e-18"},
            "effective",
            "enforce",
            "local",
        ),
        (
            {"policy": "enforce", "value": "1e-19"},
            {"policy": "ignore"},
            "ignored",
            None,
            "local_ignore",
        ),
        (None, {"policy": "ignore"}, "ignored", None, "local_ignore"),
        (None, None, "unmanaged", None, "unmanaged"),
    ),
)
def test_resolution_truth_table_and_provenance(
    base_directive,
    local_directive,
    state,
    policy,
    provenance,
):
    base_settings = (
        {"damage_slider": base_directive} if base_directive is not None else {}
    )
    local_settings = (
        {"damage_slider": local_directive} if local_directive is not None else {}
    )
    base = _base_snapshot(settings=base_settings)
    source = _custom_source(
        "matrix_strategy",
        base={"id": "farm_base", "revision": 1},
        settings=local_settings,
    )

    result = resolve_strategy_source(source, base)["settings"]["damage_slider"]

    assert result["state"] == state
    assert result.get("policy") == policy
    assert result["provenance"]["kind"] == provenance
    if provenance == "base":
        assert result["provenance"] == {
            "kind": "base",
            "base_id": "farm_base",
            "revision": 1,
        }
        assert result["value"] == "1E-19%"
    if base_directive is not None and provenance == "local":
        assert result["overridden_base"]["value"] == "1E-19%"
    if base_directive is not None and provenance == "local_ignore":
        assert result["masked_base"]["value"] == "1E-19%"


def test_ignore_dormant_value_is_not_resolved_or_used_by_the_farm_plan():
    first = _bundled_authoring()
    first["id"] = "ignored_damage"
    first["display_name"] = "Ignored Damage"
    first["settings"]["damage_slider"] = {
        "policy": "ignore",
        "value": "1e-18",
    }
    second = copy.deepcopy(first)
    second["settings"]["damage_slider"]["value"] = "1e-17"

    first = normalize_strategy_source(first)
    second = normalize_strategy_source(second)
    first_resolution = resolve_strategy_source(first)
    second_resolution = resolve_strategy_source(second)

    assert first_resolution == second_resolution
    ignored = first_resolution["settings"]["damage_slider"]
    assert ignored == {
        "state": "ignored",
        "provenance": {"kind": "local_ignore"},
    }
    assert build_strategy_yaml(
        farm_source_from_resolution(first, first_resolution)
    ) == build_strategy_yaml(farm_source_from_resolution(second, second_resolution))


def test_authoring_validation_rejects_unknown_policies_settings_and_dependencies():
    with pytest.raises(StrategyAuthoringError, match="unknown setting ids"):
        normalize_strategy_source(
            _custom_source(
                "unknown_setting",
                settings={"executor_tap": {"policy": "enforce", "value": 1}},
            )
        )

    with pytest.raises(StrategyAuthoringError, match="policy must be one of"):
        normalize_strategy_source(
            _custom_source(
                "invalid_policy",
                settings={
                    "auto_pick_perks": {"policy": "observe", "value": True}
                },
            )
        )

    with pytest.raises(StrategyAuthoringError, match="cannot use ignore"):
        _base_snapshot(
            settings={"damage_slider": {"policy": "ignore", "value": "1e-19"}}
        )

    dependency_source = _custom_source(
        "missing_dependency",
        settings={"perk_bans": {"policy": "enforce", "value": []}},
    )
    with pytest.raises(StrategyAuthoringError, match="auto_pick_perks"):
        resolve_strategy_source(dependency_source)


def test_legacy_preserve_and_skips_become_explicit_local_ignores():
    legacy = _yaml(STRATEGY_DIRECTORY / "farm_t18.source.yaml")
    legacy["loadout"]["modules"] = {"mode": "preserve"}
    legacy["setup"] = {
        "skipped_checks": ["perk_bans"],
        "settings": {"perk_bans": ["interest"]},
    }

    source = legacy_farm_source_to_strategy_source(legacy)

    assert "base" not in source
    assert source["settings"]["modules"] == {"policy": "ignore"}
    assert source["settings"]["perk_bans"] == {
        "policy": "ignore",
        "value": ["interest"],
    }
    assert source["settings"]["cards_deck"] == {
        "policy": "enforce",
        "value": "Farm",
    }


def test_strategy_rejects_multiple_missing_and_incompatible_bases(tmp_path):
    full = _bundled_authoring()
    full.update(id="missing_base", display_name="Missing Base")
    full["base"] = {"id": "farm_base", "revision": 99}
    store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        base_directory=tmp_path / "bases",
    )
    with pytest.raises(StrategyProfileError, match="unavailable"):
        store.validate(full)

    invalid_multiple = _custom_source("multiple_bases")
    invalid_multiple["base"] = [
        {"id": "farm_base", "revision": 1},
        {"id": "other_base", "revision": 1},
    ]
    with pytest.raises(StrategyAuthoringError, match="one object"):
        normalize_strategy_source(invalid_multiple)

    source = _custom_source(
        "incompatible_base",
        base={"id": "farm_base", "revision": 1},
    )
    incompatible = _base_snapshot(family="tournament")
    with pytest.raises(StrategyAuthoringError, match="incompatible"):
        resolve_strategy_source(source, incompatible)


def test_base_revisions_are_immutable_versioned_sparse_and_stale_protected(tmp_path):
    store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        base_directory=tmp_path / "bases",
    )
    first = store.publish_base(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": "farm",
            "settings": {
                "damage_slider": {"policy": "enforce", "value": "1e-19"}
            },
        }
    )
    first_path = tmp_path / "bases" / "farm_base.base.1.yaml"
    original_bytes = first_path.read_bytes()

    with pytest.raises(StrategyProfileConflictError, match="changed after"):
        store.publish_base(
            {
                "id": "farm_base",
                "display_name": "Farm Base",
                "family": "farm",
                "settings": {},
            },
            expected_latest_fingerprint="stale",
        )

    second = store.publish_base(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": "farm",
            "settings": {
                "damage_slider": {"policy": "observe", "value": "1e-18"}
            },
        },
        expected_latest_fingerprint=first["source_fingerprint"],
    )

    assert first["snapshot"]["revision"] == 1
    assert second["snapshot"]["revision"] == 2
    assert first_path.read_bytes() == original_bytes
    assert (tmp_path / "bases" / "farm_base.base.2.yaml").is_file()
    assert "farm_base" not in configurable_strategy_ids(tmp_path / "profiles")


def test_publication_embeds_pinned_snapshot_and_stays_stable_after_new_base(
    tmp_path,
    monkeypatch,
):
    profile_directory = tmp_path / "profiles"
    base_directory = tmp_path / "bases"
    store = StrategyProfileStore(
        profile_directory=profile_directory,
        base_directory=base_directory,
    )
    bundled = _bundled_authoring()
    base_settings = copy.deepcopy(bundled["settings"])
    first_base = store.publish_base(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": "farm",
            "settings": base_settings,
        }
    )
    sparse_strategy = _custom_source(
        "pinned_strategy",
        base={"id": "farm_base", "revision": 1},
    )

    published = store.publish(sparse_strategy)
    publication_path = profile_directory / "pinned_strategy.profile.yaml"
    stored = _yaml(publication_path)
    original_plan = copy.deepcopy(stored["plan"])

    assert stored["schema_version"] == 2
    assert stored["base_snapshot"] == first_base["snapshot"]
    for fingerprint_name in (
        "source_fingerprint",
        "base_fingerprint",
        "resolution_fingerprint",
        "plan_fingerprint",
    ):
        assert len(stored[fingerprint_name]) == 64
    assert published["profile"]["version"] == 1

    base_settings["damage_slider"] = {
        "policy": "enforce",
        "value": "1e-17",
    }
    store.publish_base(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": "farm",
            "settings": base_settings,
        },
        expected_latest_fingerprint=first_base["source_fingerprint"],
    )

    def mutable_base_must_not_load(*args, **kwargs):
        raise AssertionError("runtime plan loading consulted the mutable base store")

    monkeypatch.setattr(StrategyBaseStore, "load", mutable_base_must_not_load)
    monkeypatch.setattr(
        strategy_profiles_module,
        "farm_source_from_resolution",
        mutable_base_must_not_load,
    )
    monkeypatch.setattr(
        strategy_profiles_module,
        "build_strategy_yaml",
        mutable_base_must_not_load,
    )
    assert load_published_strategy_plan(
        "pinned_strategy", profile_directory
    ) == original_plan
    reread = _yaml(publication_path)
    assert reread["base_snapshot"]["revision"] == 1
    assert reread["base_fingerprint"] == stored["base_fingerprint"]


@pytest.mark.parametrize("name", ("farm_t18", "farm_t19"))
def test_repository_farm_sources_resolve_through_adapter_without_plan_changes(name):
    source = _bundled_authoring(name)
    resolution = resolve_strategy_source(source)
    compact = farm_source_from_resolution(source, resolution)

    assert build_strategy_yaml(compact) == _yaml(
        STRATEGY_DIRECTORY / f"{name}.strategy.yaml"
    )


def test_schema_one_profile_converts_in_memory_without_rewrite_or_plan_change(
    tmp_path,
):
    fixture = STRATEGY_DIRECTORY / "custom" / "farm_t19_custom.profile.yaml"
    profile_directory = tmp_path / "profiles"
    profile_directory.mkdir()
    copied = profile_directory / fixture.name
    copied.write_bytes(fixture.read_bytes())
    before = copied.read_bytes()
    legacy = _yaml(copied)
    store = StrategyProfileStore(profile_directory=profile_directory)

    source = store.authoring_source("farm_t19_custom")

    assert source is not None
    assert source["schema_version"] == 2
    assert "base" not in source
    assert set(source["settings"]) == set(FARM_SETTING_REGISTRY)
    assert all(
        directive["policy"] in {"enforce", "observe", "ignore"}
        for directive in source["settings"].values()
    )
    resolution = resolve_strategy_source(source)
    compact = farm_source_from_resolution(source, resolution)
    regenerated = build_strategy_yaml(compact)
    assert regenerated == legacy["plan"]
    assert regenerated["run_configuration"] == legacy["plan"]["run_configuration"]
    assert load_published_strategy_plan(
        "farm_t19_custom", profile_directory
    ) == legacy["plan"]
    assert copied.read_bytes() == before
