from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

import core.strategy_authoring as strategy_authoring_module
from core.strategy_authoring import (
    AUTHORING_SCHEMA_VERSION,
    LEGACY_AUTHORING_SCHEMA_VERSION,
    StrategyAuthoringError,
    diff_strategy_resolutions,
    farm_source_from_resolution,
    fingerprint_document,
    legacy_farm_source_to_strategy_source,
    normalize_strategy_source,
    resolve_strategy_source,
)
from core.strategy_profiles import (
    STRATEGY_PUBLICATION_SCHEMA_VERSION,
    StrategyProfileStore,
    load_published_strategy_plan,
)
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "config" / "strategies"
LOADOUTS = ROOT / "config" / "loadouts"


def _yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _source(identifier: str = "local_loadouts") -> dict:
    source = legacy_farm_source_to_strategy_source(
        _yaml(STRATEGIES / "farm_t18.source.yaml"),
        display_name="Local Loadouts",
    )
    source.update(id=identifier, display_name="Local Loadouts", version=1)
    return source


def _definitions() -> dict[str, object]:
    return {
        "modules": copy.deepcopy(
            _yaml(LOADOUTS / "modules.yaml")["presets"]["farm_standard"]
        ),
        "target_priority": copy.deepcopy(
            _yaml(LOADOUTS / "target_priorities.yaml")["presets"]["farm_t18"]
        ),
        "orb_distance": copy.deepcopy(
            _yaml(LOADOUTS / "orb_distances.yaml")["presets"]["farm_min_range"]
        ),
    }


def _without_preset_provenance(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_preset_provenance(item)
            for key, item in value.items()
            if key != "preset"
        }
    if isinstance(value, list):
        return [_without_preset_provenance(item) for item in value]
    return value


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(
            _contains_key(item, key) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _install_mutable_loadout_catalogs(monkeypatch) -> dict[str, dict]:
    catalogs = {
        "modules": _yaml(LOADOUTS / "modules.yaml"),
        "target_priority": _yaml(LOADOUTS / "target_priorities.yaml"),
        "orb_distance": _yaml(LOADOUTS / "orb_distances.yaml"),
    }
    original = strategy_authoring_module._load_yaml_mapping

    def mutable_load(path: Path, description: str) -> dict:
        if path == strategy_authoring_module.MODULE_PRESETS_PATH:
            return copy.deepcopy(catalogs["modules"])
        if path == strategy_authoring_module.TARGET_PRIORITY_PRESETS_PATH:
            return copy.deepcopy(catalogs["target_priority"])
        if path == strategy_authoring_module.ORB_DISTANCE_PRESETS_PATH:
            return copy.deepcopy(catalogs["orb_distance"])
        return original(path, description)

    monkeypatch.setattr(strategy_authoring_module, "_load_yaml_mapping", mutable_load)
    return catalogs


def test_local_definitions_match_preset_runtime_behavior_and_keep_bundled_output():
    compact = _yaml(STRATEGIES / "farm_t18.source.yaml")
    assert build_strategy_yaml(compact) == _yaml(
        STRATEGIES / "farm_t18.strategy.yaml"
    )

    preset_source = _source()
    preset_resolution = resolve_strategy_source(preset_source)
    preset_plan = build_strategy_yaml(
        farm_source_from_resolution(preset_source, preset_resolution)
    )

    local_source = copy.deepcopy(preset_source)
    for setting_id, definition in _definitions().items():
        local_source["settings"][setting_id]["value"] = {
            "local": copy.deepcopy(definition)
        }
    local_source = normalize_strategy_source(local_source)
    local_resolution = resolve_strategy_source(local_source)
    local_plan = build_strategy_yaml(
        farm_source_from_resolution(local_source, local_resolution)
    )

    assert _without_preset_provenance(local_plan) == (
        _without_preset_provenance(preset_plan)
    )
    assert local_plan["rules"] == preset_plan["rules"]
    assert local_plan["session_preflight"] == preset_plan["session_preflight"]
    for setting_id, definition in _definitions().items():
        snapshot = local_resolution["settings"][setting_id][
            "definition_snapshot"
        ]
        assert snapshot["source"] == "local"
        assert snapshot["definition"] == definition
        assert len(snapshot["fingerprint"]) == 64
    assert local_resolution["settings"]["orb_distance"][
        "definition_snapshot"
    ]["range_relationships"] == preset_resolution["settings"][
        "orb_distance"
    ]["definition_snapshot"]["range_relationships"]


@pytest.mark.parametrize(
    ("setting_id", "value", "message"),
    (
        (
            "modules",
            {"preset": "farm_standard", "local": _definitions()["modules"]},
            "exactly one",
        ),
        ("modules", {"preset": "missing_modules"}, "unknown modules preset"),
        (
            "modules",
            {"local": {"cannon_primary": "Amplifying Strike"}},
            "every equipped slot",
        ),
        (
            "modules",
            {
                "local": {
                    **_definitions()["modules"],
                    "cannon_assist": "Amplifying Strike",
                }
            },
            "cannot repeat",
        ),
        (
            "modules",
            {
                "local": {
                    **_definitions()["modules"],
                    "cannon_primary": "Project Funding",
                }
            },
            "not cannon",
        ),
        (
            "target_priority",
            {"local": list(_definitions()["target_priority"])[1:]},
            "every target exactly once",
        ),
        (
            "target_priority",
            {"local": {item: item for item in _definitions()["target_priority"]}},
            "must be a list",
        ),
        (
            "orb_distance",
            {"local": {"range_basis": "30m", "extra": "30m"}},
            "exactly range_basis",
        ),
        (
            "orb_distance",
            {
                "local": {
                    "range_basis": "30m",
                    "extra": "-1m",
                    "workshop": "39m",
                }
            },
            "invalid Orb Distance",
        ),
    ),
)
def test_local_definition_validation_rejects_ambiguous_incomplete_and_invalid_forms(
    setting_id,
    value,
    message,
):
    source = _source(f"invalid_{setting_id}")
    source["settings"][setting_id]["value"] = value

    with pytest.raises(StrategyAuthoringError, match=message):
        resolve_strategy_source(source)


def test_base_and_strategy_share_local_model_with_inherit_override_and_ignore(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    base_settings = copy.deepcopy(_source("base_template")["settings"])
    definitions = _definitions()
    for setting_id, definition in definitions.items():
        base_settings[setting_id]["value"] = {"local": copy.deepcopy(definition)}
    base = store.publish_base(
        {
            "id": "local_base",
            "display_name": "Local Base",
            "family": "farm",
            "settings": base_settings,
        }
    )

    assert base["snapshot"]["schema_version"] == AUTHORING_SCHEMA_VERSION
    assert len(base["resolution_fingerprint"]) == 64
    for setting_id in definitions:
        assert base["resolution"]["settings"][setting_id][
            "definition_snapshot"
        ]["source"] == "local"

    target_override = copy.deepcopy(
        _yaml(LOADOUTS / "target_priorities.yaml")["presets"]["farm_t19"]
    )
    strategy = {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "kind": "strategy",
        "id": "local_inheritance",
        "display_name": "Local Inheritance",
        "family": "farm",
        "tier": 18,
        "version": 1,
        "base": {"id": "local_base", "revision": 1},
        "settings": {
            "modules": {"policy": "ignore"},
            "target_priority": {
                "policy": "observe",
                "value": {"local": target_override},
            },
        },
    }
    published = store.publish(strategy)
    publication = _yaml(tmp_path / "local_inheritance.profile.yaml")
    resolution = publication["resolution"]["settings"]

    assert resolution["modules"] == {
        "state": "ignored",
        "provenance": {"kind": "local_ignore"},
        "masked_base": base_settings["modules"],
    }
    assert resolution["target_priority"]["provenance"] == {"kind": "local"}
    assert resolution["target_priority"]["definition_snapshot"][
        "definition"
    ] == target_override
    assert resolution["orb_distance"]["provenance"] == {
        "kind": "base",
        "base_id": "local_base",
        "revision": 1,
    }
    assert resolution["orb_distance"]["definition_snapshot"] == base[
        "resolution"
    ]["settings"]["orb_distance"]["definition_snapshot"]
    assert publication["base_resolution"] == base["resolution"]
    assert published["profile"]["version"] == 1

    original_plan = copy.deepcopy(publication["plan"])
    changed_settings = copy.deepcopy(base_settings)
    changed_settings["orb_distance"]["value"] = {
        "local": {
            "range_basis": "30m",
            "extra": "31m",
            "workshop": "39m",
        }
    }
    store.publish_base(
        {
            "id": "local_base",
            "display_name": "Local Base",
            "family": "farm",
            "settings": changed_settings,
        },
        expected_latest_fingerprint=base["source_fingerprint"],
    )
    assert load_published_strategy_plan("local_inheritance", tmp_path) == original_plan


def test_definition_snapshots_are_deterministic_and_semantically_compared():
    preset_source = _source("definition_diff")
    first = resolve_strategy_source(preset_source)
    second = resolve_strategy_source(copy.deepcopy(preset_source))
    assert first == second
    assert fingerprint_document(first) == fingerprint_document(second)

    local_source = copy.deepcopy(preset_source)
    local_source["settings"]["modules"]["value"] = {
        "local": _definitions()["modules"]
    }
    local = resolve_strategy_source(local_source)
    comparison = diff_strategy_resolutions(first, local)

    changed = {
        item["setting_id"]: item for item in comparison["changed"]
    }
    assert "modules" in changed
    assert changed["modules"]["before"]["definition_snapshot"][
        "source"
    ] == "preset"
    assert changed["modules"]["after"]["definition_snapshot"][
        "source"
    ] == "local"
    assert changed["modules"]["before"]["definition_snapshot"][
        "definition"
    ] == changed["modules"]["after"]["definition_snapshot"]["definition"]


def test_preset_backed_base_revision_retains_definition_for_later_inheritance(
    tmp_path,
    monkeypatch,
):
    catalogs = _install_mutable_loadout_catalogs(monkeypatch)
    store = StrategyProfileStore(profile_directory=tmp_path)
    base_settings = copy.deepcopy(_source("preset_base_template")["settings"])
    base = store.publish_base(
        {
            "id": "preset_base",
            "display_name": "Preset Base",
            "family": "farm",
            "settings": base_settings,
        }
    )
    retained_modules = copy.deepcopy(
        base["resolution"]["settings"]["modules"]["definition_snapshot"]
    )

    catalogs["modules"]["presets"].pop("farm_standard")
    catalogs["target_priority"]["presets"].pop("farm_t18")
    catalogs["orb_distance"]["presets"].pop("farm_min_range")
    strategy = {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "kind": "strategy",
        "id": "inherited_presets",
        "display_name": "Inherited Presets",
        "family": "farm",
        "tier": 18,
        "version": 1,
        "base": {"id": "preset_base", "revision": 1},
        "settings": {},
    }

    store.publish(strategy)
    publication = _yaml(tmp_path / "inherited_presets.profile.yaml")
    assert publication["resolution"]["settings"]["modules"][
        "definition_snapshot"
    ] == retained_modules
    assert publication["base_resolution"] == base["resolution"]


def test_preset_publication_history_restore_survives_catalog_change_or_removal(
    tmp_path,
    monkeypatch,
):
    catalogs = _install_mutable_loadout_catalogs(monkeypatch)
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source("retained_presets"))
    first_revision = tmp_path / "history" / "retained_presets.strategy.1.yaml"
    exact_first_revision = first_revision.read_bytes()

    changed = _source("retained_presets")
    changed["settings"]["damage_slider"] = {
        "policy": "enforce",
        "value": "1e-18",
    }
    second = store.publish(
        changed,
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )

    catalogs["modules"]["presets"].pop("farm_standard")
    catalogs["target_priority"]["presets"].pop("farm_t18")
    catalogs["orb_distance"]["presets"].pop("farm_min_range")

    reopened = StrategyProfileStore(profile_directory=tmp_path)
    lineage = reopened.history_catalog("retained_presets")["lineages"][0]
    assert all(item["current_validation_valid"] for item in lineage["revisions"])
    selected = lineage["revisions"][1]
    detail = reopened.history_revision("retained_presets", 1)
    assert detail["expanded_plan_exposed"] is False
    assert not _contains_key(detail, "plan")
    assert detail["resolution"]["settings"]["modules"][
        "definition_snapshot"
    ]["preset"] == "farm_standard"

    preview = reopened.compare_strategy_revision(
        "retained_presets",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=second["profile"][
            "source_fingerprint"
        ],
        require_optimistic_state=True,
    )
    assert preview["valid"] is True
    assert preview["publication_activates_strategy"] is False
    restored = reopened.publish_restore_strategy(
        "retained_presets",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=second["profile"][
            "source_fingerprint"
        ],
        reviewed_restore_fingerprint=preview["reviewed_restore_fingerprint"],
    )

    assert restored["profile"]["version"] == 3
    assert first_revision.read_bytes() == exact_first_revision
    assert load_published_strategy_plan("retained_presets", tmp_path) is not None
    assert not (tmp_path / "logs" / "automation_ctl.json").exists()


def test_schema_two_publication_reader_stays_exact_while_next_draft_upgrades(
    tmp_path,
):
    source = _source("schema_two_exact")
    source["schema_version"] = LEGACY_AUTHORING_SCHEMA_VERSION
    source = normalize_strategy_source(source)
    resolution = resolve_strategy_source(source)
    plan = build_strategy_yaml(farm_source_from_resolution(source, resolution))
    publication = {
        "schema_version": STRATEGY_PUBLICATION_SCHEMA_VERSION,
        "kind": "strategy_publication",
        "id": source["id"],
        "display_name": source["display_name"],
        "published_at": "2026-08-02T09:00:00-07:00",
        "source_fingerprint": fingerprint_document(source),
        "base_fingerprint": fingerprint_document({}),
        "resolution_fingerprint": fingerprint_document(resolution),
        "plan_fingerprint": fingerprint_document(plan),
        "source": source,
        "base_snapshot": None,
        "resolution": resolution,
        "plan": plan,
    }
    path = tmp_path / "schema_two_exact.profile.yaml"
    path.write_text(yaml.safe_dump(publication, sort_keys=False), encoding="utf-8")
    exact_publication = path.read_bytes()

    store = StrategyProfileStore(profile_directory=tmp_path)
    retained_source = store.authoring_source("schema_two_exact")
    assert retained_source == source
    assert retained_source["schema_version"] == LEGACY_AUTHORING_SCHEMA_VERSION
    assert path.read_bytes() == exact_publication

    prospective = store.validate_authoring_strategy(copy.deepcopy(source))
    assert prospective["source"]["schema_version"] == AUTHORING_SCHEMA_VERSION
    for setting_id in ("modules", "target_priority", "orb_distance"):
        assert "definition_snapshot" in prospective["resolution"]["settings"][
            setting_id
        ]
    assert path.read_bytes() == exact_publication
