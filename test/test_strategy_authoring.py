from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import yaml

import core.strategy_profiles as strategy_profiles_module
from core.strategy_authoring import (
    EDITOR_METADATA_SCHEMA_VERSION,
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


def _fingerprint(value: dict) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_schema_one_profile(profile_directory: Path) -> Path:
    profile_directory.mkdir(parents=True, exist_ok=True)
    source = _yaml(STRATEGY_DIRECTORY / "farm_t19.source.yaml")
    source["meta"] = {
        **source["meta"],
        "name": "farm_t19_custom",
        "version": 1,
    }
    plan = build_strategy_yaml(source)
    publication = {
        "schema_version": 1,
        "id": "farm_t19_custom",
        "display_name": "Farm T19 Custom",
        "published_at": "2026-08-02T09:00:00-07:00",
        "source_fingerprint": _fingerprint(source),
        "plan_fingerprint": _fingerprint(plan),
        "source": source,
        "plan": plan,
    }
    path = profile_directory / "farm_t19_custom.profile.yaml"
    path.write_text(
        yaml.safe_dump(publication, sort_keys=False),
        encoding="utf-8",
    )
    return path


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
        assert item["initial_value"] == FARM_SETTING_REGISTRY[item["id"]].normalizer(
            copy.deepcopy(item["initial_value"])
        )
        assert item["editor"]["schema_version"] == EDITOR_METADATA_SCHEMA_VERSION
        assert item["editor"]["help_text"]
        assert item["dependency_display_names"] == [
            FARM_SETTING_REGISTRY[dependency].display_name
            for dependency in item["dependencies"]
        ]
        assert not {
            "normalizer",
            "adapter",
            "initial_value_factory",
            "editor_metadata_factory",
            "generated_plan",
            "actions",
        } & set(item)
        json.dumps(item, allow_nan=False)

    assert FARM_SETTING_REGISTRY["perk_auto_pick_order"].dependencies == (
        "auto_pick_perks",
    )
    assert FARM_SETTING_REGISTRY["damage_slider"].allowed_policies == (
        "enforce",
        "observe",
        "ignore",
    )


def test_registry_editor_metadata_declares_every_specialized_constraint():
    catalog = {item["id"]: item for item in setting_registry_catalog()}

    assert {
        item["editor_type"] for item in catalog.values()
    } == {
        "fixed_value",
        "boolean",
        "preset",
        "damage_percentage",
        "card_recharge_modes",
        "ordered_list",
        "perk_multiselect",
        "perk_order",
        "ultimate_weapon_toggles",
    }
    recharge = catalog["card_recharge_modes"]["editor"]
    assert [field["key"] for field in recharge["fields"]] == [
        "Demon Mode",
        "Nuke",
    ]
    assert {
        option["value"]
        for field in recharge["fields"]
        for option in field["options"]
    } == {"auto_reactivate", "ready_after_recharge"}

    free_locks = catalog["free_upgrade_locks"]["editor"]["list_constraints"]
    assert free_locks == {
        "minimum_items": 3,
        "maximum_items": 3,
        "unique_items": True,
        "allow_add": False,
        "allow_remove": False,
        "allow_reorder": True,
        "order_significant": True,
        "exact_items": [
            "Shockwave Size",
            "Bounce Shot Targets",
            "Bounce Shot Range",
        ],
    }
    guardians = catalog["guardian_chips"]["editor"]
    assert guardians["fixed"] is True
    assert guardians["list_constraints"]["allow_reorder"] is False
    assert guardians["list_constraints"]["order_significant"] is False

    auto_pick = catalog["auto_pick_perks"]["editor"]
    assert auto_pick["fixed"] is True
    assert auto_pick["options"] == [
        {"value": True, "display_name": "Enabled"}
    ]
    perk_bans = catalog["perk_bans"]["editor"]
    assert perk_bans["list_constraints"]["maximum_items"] == 6
    assert perk_bans["list_constraints"]["allow_reorder"] is False
    perk_order = catalog["perk_auto_pick_order"]["editor"]
    assert perk_order["list_constraints"]["minimum_items"] == 1
    assert perk_order["list_constraints"]["allow_reorder"] is True
    assert len(perk_order["options"]) > len(
        catalog["perk_auto_pick_order"]["initial_value"]
    )

    ultimate = catalog["ultimate_weapons"]["editor"]
    assert ultimate["preserve_unknown_fields"] is True
    assert ultimate["minimum_selected_groups"] == 1
    poison = next(
        group for group in ultimate["groups"] if group["key"] == "Poison Swamp"
    )
    stun = next(field for field in poison["fields"] if field["key"] == "stun")
    assert stun["fixed"] is True
    assert stun["options"] == [{"value": "off", "display_name": "Off"}]
    assert all(group["preserve_unknown_fields"] for group in ultimate["groups"])

    for setting_id in ("modules", "orb_distance", "target_priority"):
        preset = catalog[setting_id]["editor"]
        assert preset["fields"][0]["key"] == "preset"
        assert preset["fields"][0]["options"]
        assert preset["local_editor"]["schema_version"] == 1
        assert preset["local_editor"]["key"] == "local"
        assert preset["local_editor"]["initial_value"]

    modules = catalog["modules"]["editor"]["local_editor"]
    assert modules["value_kind"] == "object"
    assert modules["unique_field_values"] is True
    assert len(modules["fields"]) == 8
    assert set(modules["initial_value"]) == {
        field["key"] for field in modules["fields"]
    }
    assert all(field["options"] for field in modules["fields"])

    target_priority = catalog["target_priority"]["editor"]["local_editor"]
    assert target_priority["value_kind"] == "array"
    assert target_priority["list_constraints"] == {
        "minimum_items": 10,
        "maximum_items": 10,
        "unique_items": True,
        "allow_add": False,
        "allow_remove": False,
        "allow_reorder": True,
        "order_significant": True,
        "exact_items": target_priority["initial_value"],
    }
    assert {
        option["value"] for option in target_priority["options"]
    } == set(target_priority["initial_value"])

    orb_distance = catalog["orb_distance"]["editor"]["local_editor"]
    assert orb_distance["value_kind"] == "object"
    assert orb_distance["server_normalized_text"] is True
    assert [field["key"] for field in orb_distance["fields"]] == [
        "range_basis",
        "extra",
        "workshop",
    ]
    assert all(field["options"] == [] for field in orb_distance["fields"])
    damage = catalog["damage_slider"]["editor"]
    assert damage["server_normalized_text"] is True


def test_profile_local_metadata_is_additive_to_revision_23_preset_contract():
    catalog = {item["id"]: item for item in setting_registry_catalog()}

    for setting_id in ("modules", "target_priority", "orb_distance"):
        item = catalog[setting_id]
        assert item["editor_type"] == "preset"
        assert set(item["initial_value"]) == {"preset"}
        editor = copy.deepcopy(item["editor"])
        local_editor = editor.pop("local_editor")

        # This is the complete shape consumed by the revision-23 native client.
        assert editor["value_kind"] == "object"
        assert editor["preserve_unknown_fields"] is False
        assert [field["key"] for field in editor["fields"]] == ["preset"]
        assert editor["fields"][0]["initial_value"] == item["initial_value"][
            "preset"
        ]
        assert any(
            option["value"] == item["initial_value"]["preset"]
            for option in editor["fields"][0]["options"]
        )

        # A revision-23 preset reader can still round-trip presets, while a local
        # selector has no preset field for it to reinterpret or synthesize.
        preset_value = item["initial_value"]
        assert preset_value["preset"] in {
            option["value"] for option in editor["fields"][0]["options"]
        }
        assert "preset" not in {"local": local_editor["initial_value"]}

        serialized = json.dumps(local_editor, allow_nan=False)
        for server_owned_name in (
            "definition_snapshot",
            "fingerprint",
            "generated_plan",
            "actions",
            "template_path",
        ):
            assert server_owned_name not in serialized


@pytest.mark.parametrize(
    ("setting_id", "mutate", "message"),
    (
        (
            "cards_deck",
            lambda metadata: metadata.update(schema_version=999),
            "schema version",
        ),
        (
            "auto_pick_perks",
            lambda metadata: metadata["options"].append(
                {"value": False, "display_name": "Disabled"}
            ),
            "auto_pick_perks",
        ),
        (
            "card_recharge_modes",
            lambda metadata: metadata["fields"].pop(),
            "complete initial value",
        ),
        (
            "free_upgrade_locks",
            lambda metadata: metadata["list_constraints"].update(
                allow_remove=True
            ),
            "exact list",
        ),
        (
            "ultimate_weapons",
            lambda metadata: metadata.update(preserve_unknown_fields=False),
            "preserve unknown",
        ),
        (
            "damage_slider",
            lambda metadata: metadata.update(server_normalized_text=False),
            "server-normalized",
        ),
        (
            "modules",
            lambda metadata: metadata["local_editor"]["fields"].pop(),
            "complete local initial value",
        ),
        (
            "modules",
            lambda metadata: metadata["local_editor"].update(
                unique_field_values=False
            ),
            "does not declare",
        ),
        (
            "modules",
            lambda metadata: metadata["local_editor"]["fields"][0][
                "options"
            ].append(
                copy.deepcopy(
                    metadata["local_editor"]["fields"][2]["options"][0]
                )
            ),
            "not cannon",
        ),
        (
            "target_priority",
            lambda metadata: metadata["local_editor"][
                "list_constraints"
            ].update(allow_remove=True),
            "cannot add or remove",
        ),
        (
            "orb_distance",
            lambda metadata: metadata["local_editor"]["fields"].pop(),
            "complete local initial value",
        ),
    ),
)
def test_registry_rejects_invalid_serialized_editor_metadata(
    setting_id,
    mutate,
    message,
):
    definition = FARM_SETTING_REGISTRY[setting_id]
    initial = definition.normalizer(definition.initial_value_factory())
    metadata = copy.deepcopy(definition.editor_metadata_factory(initial))
    mutate(metadata)
    invalid = replace(
        definition,
        editor_metadata_factory=lambda _initial: metadata,
    )

    with pytest.raises(StrategyAuthoringError, match=message):
        invalid.catalog_item()


def test_every_server_initial_value_constructs_valid_base_and_strategy_directives():
    for item in setting_registry_catalog():
        directive = {
            "policy": "enforce",
            "value": copy.deepcopy(item["initial_value"]),
        }
        base = normalize_base_source(
            {
                "id": "initial_base",
                "display_name": "Initial Base",
                "family": "farm",
                "revision": 1,
                "settings": {item["id"]: directive},
            }
        )
        strategy = normalize_strategy_source(
            _custom_source(
                "initial_strategy",
                settings={item["id"]: directive},
            )
        )

        assert base["settings"][item["id"]]["value"] == item["initial_value"]
        assert strategy["settings"][item["id"]]["value"] == item["initial_value"]


def test_ultimate_weapon_unknown_values_are_normalized_losslessly():
    value = {
        "Poison Swamp": {
            "primary": "on",
            "stun": "off",
            "future_toggle": "on",
        },
        "Future Beam": {"primary": "off", "future_mode": "on"},
    }

    assert FARM_SETTING_REGISTRY["ultimate_weapons"].normalizer(value) == value


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


def test_schema_one_profile_converts_to_current_draft_without_rewrite_or_plan_change(
    tmp_path,
):
    profile_directory = tmp_path / "profiles"
    copied = _write_schema_one_profile(profile_directory)
    before = copied.read_bytes()
    legacy = _yaml(copied)
    store = StrategyProfileStore(profile_directory=profile_directory)

    source = store.authoring_source("farm_t19_custom")

    assert source is not None
    # Schema-1 is an editable compact facade, so the in-memory prospective
    # draft crosses the explicit schema-3 self-containment boundary.  The
    # retained publication itself remains byte-for-byte historical evidence.
    assert source["schema_version"] == 3
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
