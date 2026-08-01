from __future__ import annotations

import copy
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
    assert catalog["strategies"]["items"][2]["authoring_supported"] is False
    assert catalog["capabilities"]["publication_activates_strategy"] is False
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
        "observation_supported" in item and "repair_supported" in item
        for item in catalog["setting_registry"]
    )
    _assert_no_expanded_plan(catalog)


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


def test_authoring_api_opens_schema_one_profile_without_rewriting_it(tmp_path):
    fixture = STRATEGIES / "custom" / "farm_t19_custom.profile.yaml"
    profile_directory = tmp_path / "profiles"
    profile_directory.mkdir()
    copied = profile_directory / fixture.name
    copied.write_bytes(fixture.read_bytes())
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
    assert item["source"]["schema_version"] == 2
    assert item["source"]["kind"] == "strategy"
    assert item["editable"] is True
    assert item["resolution"]["settings"]["ultimate_weapons"]["state"] == (
        "effective"
    )
    assert copied.read_bytes() == before


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
        assert server_status["server_revision"] == CONTROL_SURFACE_REVISION == 19
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
