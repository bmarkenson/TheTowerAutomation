from __future__ import annotations

import copy
import hashlib
import http.client
import json
from pathlib import Path
import threading
from typing import Any, Mapping

import pytest
import yaml

from core.control_surface import (
    CONTROL_SURFACE_CAPABILITIES,
    CONTROL_SURFACE_REVISION,
    ControlSurfaceRequestError,
    ControlSurfaceService,
)
from core.strategy_authoring import legacy_farm_source_to_strategy_source
from tools.control_surface_server import ControlSurfaceHTTPServer, STATIC_DIR
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "config" / "strategies"


def _yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _full_settings() -> dict[str, Any]:
    source = legacy_farm_source_to_strategy_source(
        _yaml(STRATEGIES / "farm_t18.source.yaml"),
        display_name="Farm Base",
    )
    return copy.deepcopy(source["settings"])


def _base_source(
    settings: Mapping[str, Any],
    *,
    revision: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "kind": "base",
        "id": "farm_base",
        "display_name": "Farm Base",
        "family": "farm",
        "revision": revision,
        "settings": copy.deepcopy(dict(settings)),
    }


def _strategy_source(*, revision: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "kind": "strategy",
        "id": "farm_authored",
        "display_name": "Farm Authored",
        "family": "farm",
        "tier": 18,
        "version": 1,
        "base": {"id": "farm_base", "revision": revision},
        "settings": {
            "target_priority": {
                "policy": "enforce",
                "value": {"preset": "farm_t19"},
            },
            "orb_distance": {
                "policy": "ignore",
                "value": {"preset": "farm_min_range"},
            },
        },
    }


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_schema_one_profile(profile_directory: Path) -> Path:
    """Create a deterministic legacy fixture outside operator-owned catalogs."""

    profile_directory.mkdir(parents=True, exist_ok=True)
    source = _yaml(STRATEGIES / "farm_t19.source.yaml")
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


def _service(tmp_path: Path) -> ControlSurfaceService:
    return ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=tmp_path / "profiles",
    )


def _assert_no_expanded_plan(value: object) -> None:
    if isinstance(value, Mapping):
        assert "plan" not in value
        assert "rules" not in value
        for nested in value.values():
            _assert_no_expanded_plan(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_expanded_plan(nested)


def test_authoring_catalog_separates_bases_strategies_and_registry(tmp_path):
    service = _service(tmp_path)

    catalog = service.strategy_authoring()

    assert catalog["schema_version"] == 1
    assert catalog["bases"] == {"items": [], "errors": []}
    assert [item["id"] for item in catalog["strategies"]["items"]] == [
        "farm_t18",
        "farm_t19",
        "tournament",
        "none",
    ]
    assert catalog["strategies"]["items"][0]["resolution"]["settings"][
        "damage_slider"
    ]["provenance"] == {"kind": "local"}
    tournament = catalog["strategies"]["items"][2]
    assert tournament["authoring_supported"] is True
    assert tournament["editable"] is False
    assert tournament["source"]["family"] == "tournament"
    assert tournament["source"]["tier"] is None
    assert set(tournament["resolution"]["settings"]) == {
        "modules",
        "orb_distance",
    }
    assert catalog["capabilities"]["publication_activates_strategy"] is False
    assert catalog["capabilities"]["profile_local_loadout_editors"] is True
    assert catalog["capabilities"]["preset_local_copy"] is True
    assert catalog["capabilities"]["managed_custom_module_presets"] is True
    assert catalog["capabilities"]["operations"] == [
        "validate_base",
        "publish_base",
        "validate_strategy",
        "publish_strategy",
        "preview_rebase",
        "retire_strategy",
        "compare_strategy_revision",
        "preview_restore_strategy",
        "publish_restore_strategy",
        "materialize_loadout_preset",
        "create_module_preset",
    ]
    assert [state["id"] for state in catalog["capabilities"]["base_source_states"]] == [
        "not_included",
        "included_enforce",
        "included_observe",
    ]
    assert [
        state["id"] for state in catalog["capabilities"]["strategy_source_states"]
    ] == ["inherit", "override_enforce", "override_observe", "ignore"]
    assert {item["section"] for item in catalog["setting_registry"]} >= {
        "Setup",
        "Perks",
        "Loadout",
        "Ultimate Weapons",
    }
    assert all(
        "observation_supported" in item
        and "repair_supported" in item
        and item["supported_families"]
        for item in catalog["setting_registry"]
    )
    assert all(
        "initial_value" in item
        and item["editor"]["schema_version"] == 1
        and item["editor"]["help_text"]
        for item in catalog["setting_registry"]
    )
    assert {
        item["editor_type"] for item in catalog["setting_registry"]
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
    local_editors = {
        item["id"]: item["editor"]["local_editor"]
        for item in catalog["setting_registry"]
        if "local_editor" in item["editor"]
    }
    assert set(local_editors) == {"modules", "target_priority", "orb_distance"}
    assert all(
        len(
            next(
                item
                for item in catalog["setting_registry"]
                if item["id"] == setting_id
            )["editor"]["preset_catalog_fingerprint"]
        )
        == 64
        for setting_id in local_editors
    )
    modules_registry = next(
        item for item in catalog["setting_registry"] if item["id"] == "modules"
    )
    assert modules_registry["editor"]["preset_catalog"] == "module_presets"
    assert catalog["module_presets"]["id"] == "module_presets"
    assert all(
        set(option) == {"value", "display_name"}
        for option in modules_registry["editor"]["fields"][0]["options"]
    )
    assert len(local_editors["modules"]["fields"]) == 8
    assert local_editors["modules"]["unique_field_values"] is True
    assert local_editors["modules"]["repeatable_field_values"] == ["empty"]
    assert all(
        field["options"][0] == {
            "value": "empty",
            "display_name": "Empty",
        }
        for field in local_editors["modules"]["fields"]
    )
    assert local_editors["target_priority"]["list_constraints"][
        "exact_items"
    ] == local_editors["target_priority"]["initial_value"]
    assert [
        field["key"] for field in local_editors["orb_distance"]["fields"]
    ] == ["range_basis", "extra", "workshop"]
    assert [item["id"] for item in catalog["module_presets"]["items"]] == [
        "farm_standard",
        "tournament_standard",
    ]
    assert all(
        len(item["slots"]) == 8
        and item["editable"] is False
        and item["can_create_variant"] is True
        for item in catalog["module_presets"]["items"]
    )
    _assert_no_expanded_plan(catalog)


def test_api_local_editor_initial_values_validate_exactly_without_control_mutation(
    tmp_path,
):
    service = _service(tmp_path)
    before_control = service.control_store.status()
    catalog = service.strategy_authoring()
    registry = {item["id"]: item for item in catalog["setting_registry"]}
    settings = {
        setting_id: {
            "policy": "enforce",
            "value": {
                "local": copy.deepcopy(
                    registry[setting_id]["editor"]["local_editor"][
                        "initial_value"
                    ]
                )
            },
        }
        for setting_id in ("modules", "target_priority", "orb_distance")
    }
    source = _base_source(settings)
    source["schema_version"] = 3

    response = service.apply_strategy_authoring(
        {"operation": "validate_base", "source": source}
    )

    assert response["published"] is False
    for setting_id, directive in settings.items():
        assert response["source"]["settings"][setting_id]["value"] == directive[
            "value"
        ]
        snapshot = response["resolution"]["settings"][setting_id][
            "definition_snapshot"
        ]
        assert snapshot["source"] == "local"
        assert snapshot["definition"] == directive["value"]["local"]
    assert service.control_store.status() == before_control
    assert not (tmp_path / "profiles" / "bases").exists()
    _assert_no_expanded_plan(response)


@pytest.mark.parametrize(
    ("setting_id", "preset_id", "catalog_path"),
    (
        ("modules", "tournament_standard", None),
        (
            "target_priority",
            "farm_t19",
            ROOT / "config" / "loadouts" / "target_priorities.yaml",
        ),
        (
            "orb_distance",
            "tournament_range_98_38",
            ROOT / "config" / "loadouts" / "orb_distances.yaml",
        ),
    ),
)
def test_materialize_exact_selected_non_default_preset_without_mutation(
    tmp_path,
    setting_id,
    preset_id,
    catalog_path,
):
    service = _service(tmp_path)
    before_control = service.control_store.status()
    catalog = service.strategy_authoring()
    registry = {
        item["id"]: item for item in catalog["setting_registry"]
    }
    if setting_id == "modules":
        expected = next(
            item["definition"]
            for item in catalog["module_presets"]["items"]
            if item["id"] == preset_id
        )
    else:
        expected = _yaml(catalog_path)["presets"][preset_id]

    response = service.apply_strategy_authoring(
        {
            "operation": "materialize_loadout_preset",
            "setting_id": setting_id,
            "preset": preset_id,
            "expected_catalog_fingerprint": registry[setting_id]["editor"][
                "preset_catalog_fingerprint"
            ],
        }
    )

    assert response["operation"] == "materialize_loadout_preset"
    assert response["valid"] is True
    assert response["published"] is False
    assert response["publication_activates_strategy"] is False
    assert response["materialization"]["setting_id"] == setting_id
    assert response["materialization"]["preset"] == preset_id
    assert response["materialization"]["definition"] == expected
    assert len(response["materialization"]["definition_fingerprint"]) == 64
    assert service.control_store.status() == before_control
    assert not (tmp_path / "profiles" / "bases").exists()
    assert not list((tmp_path / "profiles").glob("*.profile.yaml"))
    _assert_no_expanded_plan(response)


def test_preset_materialization_rejects_unknown_stale_and_invalid_catalogs(
    tmp_path,
    monkeypatch,
):
    service = _service(tmp_path)
    before_control = service.control_store.status()
    initial = service.strategy_authoring()
    modules = next(
        item for item in initial["setting_registry"] if item["id"] == "modules"
    )
    initial_fingerprint = modules["editor"]["preset_catalog_fingerprint"]

    with pytest.raises(ControlSurfaceRequestError, match="unknown modules preset") as unknown:
        service.apply_strategy_authoring(
            {
                "operation": "materialize_loadout_preset",
                "setting_id": "modules",
                "preset": "missing_preset",
                "expected_catalog_fingerprint": initial_fingerprint,
            }
        )
    assert unknown.value.status == 400

    service.apply_strategy_authoring(
        {
            "operation": "create_module_preset",
            "id": "catalog_change",
            "display_name": "Catalog Change",
            "source": {"preset": "farm_standard"},
        }
    )
    with pytest.raises(ControlSurfaceRequestError, match="changed after it was opened") as stale:
        service.apply_strategy_authoring(
            {
                "operation": "materialize_loadout_preset",
                "setting_id": "modules",
                "preset": "farm_standard",
                "expected_catalog_fingerprint": initial_fingerprint,
            }
        )
    assert stale.value.status == 409

    invalid_catalog = copy.deepcopy(service.profile_store.module_preset_store.catalog())
    invalid_item = next(
        item
        for item in invalid_catalog["items"]
        if item["id"] == "tournament_standard"
    )
    invalid_item["definition"]["cannon_primary"] = invalid_item["definition"][
        "cannon_assist"
    ]
    monkeypatch.setattr(
        service.profile_store.module_preset_store,
        "catalog",
        lambda: copy.deepcopy(invalid_catalog),
    )
    invalid_registry = service.strategy_authoring()
    invalid_modules = next(
        item
        for item in invalid_registry["setting_registry"]
        if item["id"] == "modules"
    )
    with pytest.raises(ControlSurfaceRequestError, match="preset .* is invalid") as invalid:
        service.apply_strategy_authoring(
            {
                "operation": "materialize_loadout_preset",
                "setting_id": "modules",
                "preset": "tournament_standard",
                "expected_catalog_fingerprint": invalid_modules["editor"][
                    "preset_catalog_fingerprint"
                ],
            }
        )
    assert invalid.value.status == 400
    assert service.control_store.status() == before_control
    assert not list((tmp_path / "profiles" / "bases").glob("*.yaml"))
    assert not list((tmp_path / "profiles").glob("*.profile.yaml"))


def test_managed_module_preset_creation_refreshes_all_catalogs_without_publication(
    tmp_path,
):
    service = _service(tmp_path)
    before_control = service.control_store.status()
    initial = service.strategy_authoring()
    farm_definition = copy.deepcopy(
        next(
            item
            for item in initial["module_presets"]["items"]
            if item["id"] == "farm_standard"
        )["definition"]
    )

    bundled_variant = service.apply_strategy_authoring(
        {
            "operation": "create_module_preset",
            "id": "farm_visible_variant",
            "display_name": "Farm Visible Variant",
            "source": {"preset": "farm_standard"},
        }
    )
    local_definition = copy.deepcopy(farm_definition)
    local_definition["generator_primary"] = "Project Funding"
    local_variant = service.apply_strategy_authoring(
        {
            "operation": "create_module_preset",
            "id": "project_local_variant",
            "display_name": "Project Local Variant",
            "source": {"local": local_definition},
        }
    )

    assert bundled_variant["valid"] is True
    assert bundled_variant["published"] is False
    assert bundled_variant["publication_activates_strategy"] is False
    assert bundled_variant["preset"]["definition"] == farm_definition
    assert local_variant["preset"]["definition"] == local_definition
    assert local_variant["preset"]["origin"] == "custom"
    refreshed = local_variant["catalog"]
    module_ids = [item["id"] for item in refreshed["module_presets"]["items"]]
    assert module_ids == [
        "farm_standard",
        "tournament_standard",
        "farm_visible_variant",
        "project_local_variant",
    ]
    module_registry = next(
        item for item in refreshed["setting_registry"] if item["id"] == "modules"
    )
    assert [
        option["value"]
        for option in module_registry["editor"]["fields"][0]["options"]
    ] == module_ids
    assert [
        item["id"] for item in service.strategy_profiles()["presets"]["modules"]
    ] == module_ids
    assert service.control_store.status() == before_control
    assert not list((tmp_path / "profiles").glob("*.profile.yaml"))
    assert not list((tmp_path / "profiles" / "bases").glob("*.yaml"))
    _assert_no_expanded_plan(refreshed)
    assert "path" not in json.dumps(refreshed).lower()


def test_module_preset_api_returns_structured_validation_and_conflict_errors(
    tmp_path,
):
    service = _service(tmp_path)
    server = ControlSurfaceHTTPServer(
        ("127.0.0.1", 0),
        service=service,
        token="module-preset-secret",
        static_dir=STATIC_DIR,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=5,
    )

    def create(payload: object) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload)
        connection.request(
            "POST",
            "/api/v1/strategy-authoring",
            body=body,
            headers={
                "Authorization": "Bearer module-preset-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode("utf-8"))),
            },
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        invalid_status, invalid = create(
            {
                "operation": "create_module_preset",
                "id": "Bad-ID",
                "display_name": "Bad",
                "source": {"preset": "farm_standard"},
            }
        )
        collision_status, collision = create(
            {
                "operation": "create_module_preset",
                "id": "farm_standard",
                "display_name": "Shadow",
                "source": {"preset": "farm_standard"},
            }
        )
        path_status, path_error = create(
            {
                "operation": "create_module_preset",
                "id": "path_variant",
                "display_name": "Path Variant",
                "source": {"preset": "farm_standard"},
                "filesystem_path": str(tmp_path / "outside"),
            }
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert invalid_status == 400
    assert invalid["code"] == "invalid_module_preset_id"
    assert invalid["details"] == {"field": "id"}
    assert collision_status == 409
    assert collision["code"] == "bundled_module_preset_collision"
    assert collision["details"] == {"field": "id"}
    assert path_status == 400
    assert path_error["code"] == "invalid_module_preset_request"
    assert path_error["details"] == {"field": "request"}
    combined = json.dumps(
        {"invalid": invalid, "collision": collision, "path": path_error}
    )
    assert str(tmp_path) not in combined
    assert "expanded" not in combined
    assert not (tmp_path / "profiles" / "bases").exists()


@pytest.mark.parametrize(
    ("setting_id", "mutate", "message"),
    (
        (
            "modules",
            lambda value: value.update(
                cannon_primary=value["cannon_assist"]
            ),
            "cannot repeat a module",
        ),
        (
            "target_priority",
            lambda value: value.pop(),
            "every target exactly once",
        ),
        (
            "orb_distance",
            lambda value: value.update(extra="not-a-distance"),
            "invalid Orb Distance",
        ),
    ),
)
def test_api_rejects_malformed_local_editor_values_without_writes(
    tmp_path,
    setting_id,
    mutate,
    message,
):
    service = _service(tmp_path)
    before_control = service.control_store.status()
    registry = {
        item["id"]: item for item in service.strategy_authoring()["setting_registry"]
    }
    value = copy.deepcopy(
        registry[setting_id]["editor"]["local_editor"]["initial_value"]
    )
    mutate(value)
    source = _base_source(
        {
            setting_id: {
                "policy": "enforce",
                "value": {"local": value},
            }
        }
    )
    source["schema_version"] = 3

    with pytest.raises(ControlSurfaceRequestError, match=message):
        service.apply_strategy_authoring(
            {"operation": "validate_base", "source": source}
        )

    assert service.control_store.status() == before_control
    assert not (tmp_path / "profiles" / "bases").exists()


def test_every_authoring_mutation_and_rebase_semantic_diff(tmp_path):
    service = _service(tmp_path)
    first_settings = _full_settings()
    first_settings.pop("modules")
    base = _base_source(first_settings)

    validated_base = service.apply_strategy_authoring(
        {"operation": "validate_base", "source": base}
    )
    assert validated_base["operation"] == "validate_base"
    assert validated_base["source"]["revision"] == 1
    assert validated_base["resolution"]["settings"]["damage_slider"][
        "provenance"
    ] == {"kind": "base", "base_id": "farm_base", "revision": 1}
    assert not (tmp_path / "profiles" / "bases").exists()

    published_base = service.apply_strategy_authoring(
        {"operation": "publish_base", "source": base}
    )
    assert published_base["published"] is True
    assert published_base["source"]["revision"] == 1
    assert published_base["catalog"]["bases"]["items"][0][
        "latest_revision"
    ] == 1
    assert published_base["catalog"]["bases"]["items"][0]["resolution"][
        "settings"
    ]["modules"]["state"] == "unmanaged"
    assert "farm_base" not in {
        item["id"] for item in service.strategy_profiles()["items"]
    }
    assert service.control_store.status()["strategy"] is None

    strategy = _strategy_source()
    validated_strategy = service.apply_strategy_authoring(
        {"operation": "validate_strategy", "source": strategy}
    )
    assert validated_strategy["operation"] == "validate_strategy"
    assert validated_strategy["resolution"]["settings"]["damage_slider"][
        "provenance"
    ] == {"kind": "base", "base_id": "farm_base", "revision": 1}
    assert validated_strategy["resolution"]["settings"]["target_priority"][
        "provenance"
    ] == {"kind": "local"}
    assert validated_strategy["resolution"]["settings"]["orb_distance"][
        "state"
    ] == "ignored"
    assert validated_strategy["rule_count"] > 0

    published_strategy = service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": strategy}
    )
    assert published_strategy["published"] is True
    assert published_strategy["profile"]["id"] == "farm_authored"
    assert service.control_store.status()["strategy"] is None
    _assert_no_expanded_plan(published_strategy)

    second_settings = copy.deepcopy(first_settings)
    second_settings["modules"] = {
        "policy": "enforce",
        "value": {"preset": "farm_standard"},
    }
    second_settings.pop("target_priority")
    second_settings["damage_slider"] = {
        "policy": "enforce",
        "value": "1e-17",
    }
    second_settings["orb_distance"] = {
        "policy": "enforce",
        "value": {"preset": "tournament_range_98_38"},
    }
    second_base = service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(second_settings),
            "expected_latest_fingerprint": published_base["source_fingerprint"],
        }
    )
    assert second_base["source"]["revision"] == 2

    refreshed = service.strategy_authoring()
    strategy_item = next(
        item
        for item in refreshed["strategies"]["items"]
        if item["id"] == "farm_authored"
    )
    assert strategy_item["base_update"] == {
        "id": "farm_base",
        "display_name": "Farm Base",
        "pinned_revision": 1,
        "latest_revision": 2,
        "source_fingerprint": second_base["source_fingerprint"],
    }

    preview = service.apply_strategy_authoring(
        {
            "operation": "preview_rebase",
            "source": strategy_item["source"],
            "target_base": {"id": "farm_base", "revision": 2},
        }
    )
    rebase = preview["rebase"]
    assert [item["setting_id"] for item in rebase["base_changes"]["added"]] == [
        "modules"
    ]
    assert [
        item["setting_id"] for item in rebase["base_changes"]["removed"]
    ] == ["target_priority"]
    assert {
        item["setting_id"] for item in rebase["base_changes"]["changed"]
    } == {"damage_slider", "orb_distance"}
    assert {
        item["setting_id"] for item in rebase["inherited_effective_changes"]
    } == {"modules", "damage_slider"}
    assert {
        item["setting_id"] for item in rebase["local_overrides_unchanged"]
    } == {"target_priority"}
    assert {
        item["setting_id"] for item in rebase["explicit_ignores_unchanged"]
    } == {"orb_distance"}
    assert rebase["validation_errors"] == []
    assert preview["valid"] is True
    _assert_no_expanded_plan(preview)

    rebased_source = preview["source"]
    with pytest.raises(ControlSurfaceRequestError, match="reviewed rebase") as error:
        service.apply_strategy_authoring(
            {
                "operation": "publish_strategy",
                "source": rebased_source,
                "expected_source_fingerprint": published_strategy["profile"][
                    "source_fingerprint"
                ],
            }
        )
    assert error.value.status == 400

    rebased = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": rebased_source,
            "expected_source_fingerprint": published_strategy["profile"][
                "source_fingerprint"
            ],
            "reviewed_rebase_fingerprint": preview[
                "reviewed_rebase_fingerprint"
            ],
        }
    )
    assert rebased["profile"]["version"] == 2
    assert rebased["source"]["base"] == {"id": "farm_base", "revision": 2}
    assert service.control_store.status()["strategy"] is None

    audit = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert "Published strategy Base farm_base immutable revision 1" in audit
    assert "Published Strategy farm_authored version 1; activation unchanged" in audit


def test_rebase_preview_returns_dependency_and_builder_errors(tmp_path):
    service = _service(tmp_path)
    first_settings = _full_settings()
    first = service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(first_settings),
        }
    )
    strategy = _strategy_source()
    strategy["settings"].pop("orb_distance")
    service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": strategy}
    )

    broken = copy.deepcopy(first_settings)
    broken.pop("auto_pick_perks")
    second = service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(broken),
            "expected_latest_fingerprint": first["source_fingerprint"],
        }
    )
    assert second["source"]["revision"] == 2

    preview = service.apply_strategy_authoring(
        {
            "operation": "preview_rebase",
            "source": strategy,
            "target_base": {"id": "farm_base", "revision": 2},
        }
    )

    assert preview["valid"] is False
    assert any(
        error.get("setting_id") in {"perk_bans", "perk_auto_pick_order"}
        for error in preview["rebase"]["validation_errors"]
    )
    assert all(
        error["code"] == "missing_dependency"
        for error in preview["rebase"]["validation_errors"]
    )


def test_complex_values_survive_authoring_catalog_validation_and_publication(
    tmp_path,
):
    service = _service(tmp_path)
    settings = _full_settings()
    base = service.apply_strategy_authoring(
        {"operation": "publish_base", "source": _base_source(settings)}
    )
    strategy = _strategy_source()
    strategy["settings"] = {}

    validated = service.apply_strategy_authoring(
        {"operation": "validate_strategy", "source": strategy}
    )
    published = service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": strategy}
    )
    catalog = service.strategy_authoring()
    item = next(
        item
        for item in catalog["strategies"]["items"]
        if item["id"] == "farm_authored"
    )

    expected = base["source"]["settings"]["ultimate_weapons"]["value"]
    assert validated["resolution"]["settings"]["ultimate_weapons"]["value"] == expected
    assert published["resolution"]["settings"]["ultimate_weapons"]["value"] == expected
    assert item["resolution"]["settings"]["ultimate_weapons"]["value"] == expected


def test_unknown_ultimate_weapon_values_survive_validation_publication_and_reopen(
    tmp_path,
):
    service = _service(tmp_path)
    service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(_full_settings()),
        }
    )
    unknown_value = {
        "Poison Swamp": {
            "primary": "off",
            "stun": "off",
            "future_toggle": "on",
        },
        "Future Beam": {"primary": "off", "future_mode": "on"},
    }
    strategy = _strategy_source()
    strategy["settings"]["ultimate_weapons"] = {
        "policy": "enforce",
        "value": copy.deepcopy(unknown_value),
    }

    validated = service.apply_strategy_authoring(
        {"operation": "validate_strategy", "source": strategy}
    )
    published = service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": strategy}
    )
    reopened = next(
        item
        for item in service.strategy_authoring()["strategies"]["items"]
        if item["id"] == "farm_authored"
    )

    for response in (validated, published):
        assert response["source"]["settings"]["ultimate_weapons"]["value"] == (
            unknown_value
        )
        assert response["resolution"]["settings"]["ultimate_weapons"][
            "value"
        ] == unknown_value
        _assert_no_expanded_plan(response)
    assert reopened["source"]["settings"]["ultimate_weapons"]["value"] == (
        unknown_value
    )
    assert reopened["resolution"]["settings"]["ultimate_weapons"]["value"] == (
        unknown_value
    )
    _assert_no_expanded_plan(reopened)


def test_authoring_api_opens_schema_one_profile_without_rewriting_it(tmp_path):
    profile_directory = tmp_path / "profiles"
    copied = _write_schema_one_profile(profile_directory)
    before = copied.read_bytes()
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_directory,
    )

    catalog = service.strategy_authoring()
    item = next(
        item
        for item in catalog["strategies"]["items"]
        if item["id"] == "farm_t19_custom"
    )

    assert item["legacy_converted"] is True
    assert item["source"]["schema_version"] == 3
    assert item["source"]["kind"] == "strategy"
    assert item["editable"] is True
    assert item["resolution"]["settings"]["ultimate_weapons"]["state"] == (
        "effective"
    )
    assert copied.read_bytes() == before


def test_schema_one_profile_can_attach_first_base_only_after_review(tmp_path):
    profile_directory = tmp_path / "profiles"
    copied = _write_schema_one_profile(profile_directory)
    before = copied.read_bytes()
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_directory,
    )
    service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(_full_settings()),
        }
    )
    item = next(
        candidate
        for candidate in service.strategy_authoring()["strategies"]["items"]
        if candidate["id"] == "farm_t19_custom"
    )

    preview = service.apply_strategy_authoring(
        {
            "operation": "preview_rebase",
            "source": item["source"],
            "target_base": {"id": "farm_base", "revision": 1},
        }
    )

    assert preview["valid"] is True
    assert preview["source"]["id"] == "farm_t19_custom"
    assert preview["source"]["base"] == {"id": "farm_base", "revision": 1}
    assert preview["rebase"]["base_changes"]["added"]
    assert preview["rebase"]["inherited_effective_changes"] == []
    assert copied.read_bytes() == before
    with pytest.raises(ControlSurfaceRequestError, match="reviewed rebase"):
        service.apply_strategy_authoring(
            {
                "operation": "publish_strategy",
                "source": preview["source"],
                "expected_source_fingerprint": item["source_fingerprint"],
            }
        )
    assert copied.read_bytes() == before

    published = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": preview["source"],
            "expected_source_fingerprint": item["source_fingerprint"],
            "reviewed_rebase_fingerprint": preview[
                "reviewed_rebase_fingerprint"
            ],
        }
    )

    assert published["source"]["id"] == "farm_t19_custom"
    assert published["source"]["base"] == {"id": "farm_base", "revision": 1}
    stored = _yaml(copied)
    assert stored["source"]["base"] == {"id": "farm_base", "revision": 1}
    assert stored["base_snapshot"]["id"] == "farm_base"
    assert stored["base_snapshot"]["revision"] == 1
    assert service.control_store.status()["strategy"] is None
    _assert_no_expanded_plan(published)


def test_custom_strategy_rename_and_recoverable_deletion_are_guarded(tmp_path):
    service = _service(tmp_path)
    service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(_full_settings()),
        }
    )
    published = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": _strategy_source(),
        }
    )

    renamed_source = copy.deepcopy(published["source"])
    renamed_source["display_name"] = "Farm Authored Experiment"
    renamed = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": renamed_source,
            "expected_source_fingerprint": published["profile"][
                "source_fingerprint"
            ],
        }
    )

    assert renamed["source"]["id"] == "farm_authored"
    assert renamed["source"]["display_name"] == "Farm Authored Experiment"
    assert renamed["profile"]["version"] == 2
    assert service.control_store.status()["strategy"] is None
    _assert_no_expanded_plan(renamed)

    active_path = tmp_path / "profiles" / "farm_authored.profile.yaml"
    exact_publication = active_path.read_bytes()
    service.control_store.set_strategy("farm_authored", source="test")
    with pytest.raises(ControlSurfaceRequestError) as selected_error:
        service.apply_strategy_authoring(
            {
                "operation": "retire_strategy",
                "strategy_id": "farm_authored",
                "expected_source_fingerprint": renamed["profile"][
                    "source_fingerprint"
                ],
            }
        )
    assert selected_error.value.status == 409
    assert "currently selected" in str(selected_error.value)
    assert active_path.read_bytes() == exact_publication

    service.control_store.set_strategy("farm_t18", source="test")
    retired = service.apply_strategy_authoring(
        {
            "operation": "retire_strategy",
            "strategy_id": "farm_authored",
            "expected_source_fingerprint": renamed["profile"][
                "source_fingerprint"
            ],
        }
    )

    assert retired["valid"] is True
    assert retired["published"] is False
    assert retired["retired"] is True
    assert retired["retirement"] == {
        "id": "farm_authored",
        "display_name": "Farm Authored Experiment",
        "version": 2,
        "source_fingerprint": renamed["profile"]["source_fingerprint"],
        "retired_at": retired["retirement"]["retired_at"],
        "archive_name": retired["retirement"]["archive_name"],
        "recoverable": True,
    }
    archive_path = (
        tmp_path
        / "profiles"
        / "retired"
        / retired["retirement"]["archive_name"]
    )
    assert archive_path.read_bytes() == exact_publication
    assert not active_path.exists()
    assert service.control_store.status()["strategy"] == "farm_t18"
    assert "farm_authored" not in {
        item["id"] for item in retired["catalog"]["strategies"]["items"]
    }
    assert "farm_authored" not in {
        item["id"] for item in service.strategy_profiles()["items"]
    }
    assert "Retired Strategy farm_authored version 2" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")
    _assert_no_expanded_plan(retired)


def test_schema_one_profile_publishes_only_after_explicit_review_boundary(tmp_path):
    profile_directory = tmp_path / "profiles"
    copied = _write_schema_one_profile(profile_directory)
    legacy = _yaml(copied)
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_directory,
    )
    item = next(
        candidate
        for candidate in service.strategy_authoring()["strategies"]["items"]
        if candidate["id"] == "farm_t19_custom"
    )

    published = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": item["source"],
            "expected_source_fingerprint": item["source_fingerprint"],
        }
    )
    stored = _yaml(copied)
    expected_plan = copy.deepcopy(legacy["plan"])
    expected_plan["meta"]["version"] = legacy["plan"]["meta"]["version"] + 1

    assert stored["schema_version"] == 2
    assert stored["plan"] == expected_plan
    assert published["profile"]["version"] == legacy["plan"]["meta"]["version"] + 1
    assert service.control_store.status()["strategy"] is None
    _assert_no_expanded_plan(published)


def test_base_catalog_and_publication_reject_symlink_storage(tmp_path):
    profile_directory = tmp_path / "profiles"
    base_directory = profile_directory / "bases"
    base_directory.mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("kind: not-a-base\n", encoding="utf-8")
    (base_directory / "farm_base.base.1.yaml").symlink_to(outside)
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_directory,
    )

    catalog = service.strategy_authoring()

    assert catalog["bases"]["items"] == []
    assert catalog["bases"]["errors"] == [
        {
            "id": "farm_base@1",
            "error": "symbolic-link base revisions are unsupported",
        }
    ]

    other_root = tmp_path / "other"
    other_root.mkdir()
    symlink_profiles = tmp_path / "symlink-profiles"
    symlink_profiles.mkdir()
    (symlink_profiles / "bases").symlink_to(other_root, target_is_directory=True)
    blocked = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=symlink_profiles,
    )
    with pytest.raises(ControlSurfaceRequestError, match="symbolic-link") as error:
        blocked.apply_strategy_authoring(
            {
                "operation": "publish_base",
                "source": _base_source(_full_settings()),
            }
        )
    assert error.value.status == 409


def test_history_api_comparison_restore_conflicts_audit_and_redaction(tmp_path):
    service = _service(tmp_path)
    service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(_full_settings()),
        }
    )
    first = service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": _strategy_source()}
    )
    changed = copy.deepcopy(first["source"])
    changed["settings"]["target_priority"] = {
        "policy": "enforce",
        "value": {"preset": "farm_t18"},
    }
    second = service.apply_strategy_authoring(
        {
            "operation": "publish_strategy",
            "source": changed,
            "expected_source_fingerprint": first["profile"][
                "source_fingerprint"
            ],
        }
    )
    history = service.strategy_history("farm_authored")
    selected = history["lineages"][0]["revisions"][1]
    detail = service.strategy_revision("farm_authored", 1)

    assert [item["logical_version"] for item in history["lineages"][0]["revisions"]] == [2, 1]
    assert selected["publication_origin"] == "authoring_publication"
    assert detail["revision"]["logical_version"] == 1
    _assert_no_expanded_plan(history)
    _assert_no_expanded_plan(detail)

    preview = service.apply_strategy_authoring(
        {
            "operation": "preview_restore_strategy",
            "strategy_id": "farm_authored",
            "logical_version": 1,
            "expected_revision_fingerprint": selected[
                "revision_fingerprint"
            ],
            "expected_latest_source_fingerprint": second["profile"][
                "source_fingerprint"
            ],
        }
    )
    assert preview["published"] is False
    assert preview["next_logical_version"] == 3
    assert preview["comparison"]["source_changes"]["changed"]
    assert preview["publication_activates_strategy"] is False
    _assert_no_expanded_plan(preview)

    with pytest.raises(ControlSurfaceRequestError) as stale:
        service.apply_strategy_authoring(
            {
                "operation": "publish_restore_strategy",
                "strategy_id": "farm_authored",
                "logical_version": 1,
                "expected_revision_fingerprint": selected[
                    "revision_fingerprint"
                ],
                "expected_latest_source_fingerprint": "stale",
                "reviewed_restore_fingerprint": preview[
                    "reviewed_restore_fingerprint"
                ],
            }
        )
    assert stale.value.status == 409

    restored = service.apply_strategy_authoring(
        {
            "operation": "publish_restore_strategy",
            "strategy_id": "farm_authored",
            "logical_version": 1,
            "expected_revision_fingerprint": selected[
                "revision_fingerprint"
            ],
            "expected_latest_source_fingerprint": second["profile"][
                "source_fingerprint"
            ],
            "reviewed_restore_fingerprint": preview[
                "reviewed_restore_fingerprint"
            ],
        }
    )

    assert restored["restored"] is True
    assert restored["profile"]["version"] == 3
    assert restored["history"]["lineages"][0]["revisions"][0][
        "logical_version"
    ] == 3
    assert restored["history"]["lineages"][0]["revisions"][0][
        "publication_origin"
    ] == "restore_as_new"
    assert service.control_store.status()["strategy"] is None
    _assert_no_expanded_plan(restored)
    audit = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert "Accepted reviewed restore for Strategy farm_authored" in audit
    assert "Published restored Strategy farm_authored as new latest version 3" in audit


def test_history_http_get_routes_return_summaries_without_plans(tmp_path):
    service = _service(tmp_path)
    service.apply_strategy_authoring(
        {
            "operation": "publish_base",
            "source": _base_source(_full_settings()),
        }
    )
    service.apply_strategy_authoring(
        {"operation": "publish_strategy", "source": _strategy_source()}
    )
    server = ControlSurfaceHTTPServer(
        ("127.0.0.1", 0),
        service=service,
        static_dir=STATIC_DIR,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=5,
    )
    try:
        for path in (
            "/api/v1/strategy-authoring/history",
            "/api/v1/strategy-authoring/history/farm_authored",
            "/api/v1/strategy-authoring/history/farm_authored/1",
        ):
            connection.request("GET", path)
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            _assert_no_expanded_plan(payload)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_adoption_conflict_is_preserved_reported_and_audited(tmp_path):
    profile_directory = tmp_path / "profiles"
    retired = profile_directory / "retired"
    retired.mkdir(parents=True)
    first_path = _write_schema_one_profile(tmp_path / "seed")
    first = _yaml(first_path)
    second = copy.deepcopy(first)
    second["source"]["loadout"]["damage_slider"] = {
        "mode": "enforce",
        "value": "1E-18%",
    }
    second["plan"] = build_strategy_yaml(second["source"])
    second["source_fingerprint"] = _fingerprint(second["source"])
    second["plan_fingerprint"] = _fingerprint(second["plan"])
    (retired / "first.retired.yaml").write_text(
        yaml.safe_dump(first, sort_keys=False),
        encoding="utf-8",
    )
    (retired / "second.retired.yaml").write_text(
        yaml.safe_dump(second, sort_keys=False),
        encoding="utf-8",
    )
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_directory,
    )

    history = service.strategy_history()

    assert history["lineages"] == []
    assert any("conflicting retained evidence" in item["error"] for item in history["errors"])
    audit = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert "Strategy history adoption conflict preserved" in audit
    assert (retired / "first.retired.yaml").is_file()
    assert (retired / "second.retired.yaml").is_file()


def test_authoring_http_status_codes_auth_compatibility_and_no_plan(tmp_path):
    service = _service(tmp_path)
    server = ControlSurfaceHTTPServer(
        ("127.0.0.1", 0),
        service=service,
        token="authoring-secret",
        static_dir=STATIC_DIR,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=5,
    )

    def request(method: str, path: str, payload: object | None = None):
        body = None if payload is None else json.dumps(payload)
        headers = {"Authorization": "Bearer authoring-secret"}
        if body is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body.encode("utf-8"))),
                }
            )
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        connection.request("GET", "/api/v1/strategy-authoring")
        unauthorized = connection.getresponse()
        unauthorized.read()
        assert unauthorized.status == 401

        status, catalog = request("GET", "/api/v1/strategy-authoring")
        assert status == 200
        _assert_no_expanded_plan(catalog)

        status, old_catalog = request("GET", "/api/v1/strategy-profiles")
        assert status == 200
        assert old_catalog["schema_version"] == 1
        assert old_catalog["policy_modes"] == ["enforce", "observe", "preserve"]

        status, server_status = request("GET", "/api/v1/status")
        assert status == 200
        assert server_status["server_revision"] == CONTROL_SURFACE_REVISION == 43
        assert "better_control_model_v2" in CONTROL_SURFACE_CAPABILITIES
        assert (
            "runtime_control_acknowledgements_v1"
            in CONTROL_SURFACE_CAPABILITIES
        )
        assert "save_backed_setup_capture_v1" in CONTROL_SURFACE_CAPABILITIES
        assert "save_backed_setup_capture_v2" in CONTROL_SURFACE_CAPABILITIES
        assert (
            "strategy_authoring_local_loadout_editors_v1"
            in CONTROL_SURFACE_CAPABILITIES
        )
        assert "managed_custom_module_presets_v1" in CONTROL_SURFACE_CAPABILITIES
        assert (
            "strategy_authoring_preset_local_copy_v1"
            in CONTROL_SURFACE_CAPABILITIES
        )
        assert "strategy_revision_history_v1" in CONTROL_SURFACE_CAPABILITIES
        assert (
            "strategy_authoring_profile_lifecycle_v1"
            in CONTROL_SURFACE_CAPABILITIES
        )
        assert (
            "strategy_authoring_specialized_editors_v1"
            in CONTROL_SURFACE_CAPABILITIES
        )
        assert "strategy_authoring_v1" in CONTROL_SURFACE_CAPABILITIES
        assert "strategy_profile_catalog_v1" in CONTROL_SURFACE_CAPABILITIES
        assert "strategy_profile_editor_v2" in CONTROL_SURFACE_CAPABILITIES

        status, invalid = request(
            "POST",
            "/api/v1/strategy-authoring",
            {"operation": "validate_base", "source": {"id": "../bad"}},
        )
        assert status == 400
        assert "base id" in invalid["error"]

        base_source = _base_source(_full_settings())
        status, validated_base = request(
            "POST",
            "/api/v1/strategy-authoring",
            {"operation": "validate_base", "source": base_source},
        )
        assert status == 200
        assert validated_base["published"] is False
        _assert_no_expanded_plan(validated_base)

        status, first = request(
            "POST",
            "/api/v1/strategy-authoring",
            {"operation": "publish_base", "source": base_source},
        )
        assert status == 200
        _assert_no_expanded_plan(first)

        status, stale_base = request(
            "POST",
            "/api/v1/strategy-authoring",
            {
                "operation": "publish_base",
                "source": base_source,
                "expected_latest_fingerprint": "stale",
            },
        )
        assert status == 409
        assert "changed after" in stale_base["error"]

        strategy = _strategy_source()
        status, validated_strategy = request(
            "POST",
            "/api/v1/strategy-authoring",
            {"operation": "validate_strategy", "source": strategy},
        )
        assert status == 200
        assert validated_strategy["published"] is False
        assert validated_strategy["rule_count"] > 0
        _assert_no_expanded_plan(validated_strategy)

        status, published = request(
            "POST",
            "/api/v1/strategy-authoring",
            {"operation": "publish_strategy", "source": strategy},
        )
        assert status == 200
        _assert_no_expanded_plan(published)

        status, updated_old_catalog = request(
            "GET",
            "/api/v1/strategy-profiles",
        )
        assert status == 200
        old_client_item = next(
            item
            for item in updated_old_catalog["items"]
            if item["id"] == "farm_authored"
        )
        assert old_client_item["editable"] is True
        assert old_client_item["setup"]["settings"]["cards_deck"] == "Farm"

        status, second = request(
            "POST",
            "/api/v1/strategy-authoring",
            {
                "operation": "publish_base",
                "source": base_source,
                "expected_latest_fingerprint": first["source_fingerprint"],
            },
        )
        assert status == 200
        assert second["source"]["revision"] == 2

        status, preview = request(
            "POST",
            "/api/v1/strategy-authoring",
            {
                "operation": "preview_rebase",
                "source": published["source"],
                "target_base": {"id": "farm_base", "revision": 2},
            },
        )
        assert status == 200
        assert preview["valid"] is True
        assert preview["source"]["base"]["revision"] == 2
        _assert_no_expanded_plan(preview)

        strategy["settings"]["damage_slider"] = {
            "policy": "enforce",
            "value": "1e-18",
        }
        status, stale_strategy = request(
            "POST",
            "/api/v1/strategy-authoring",
            {
                "operation": "publish_strategy",
                "source": strategy,
                "expected_source_fingerprint": "stale",
            },
        )
        assert status == 409
        assert "changed after" in stale_strategy["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
