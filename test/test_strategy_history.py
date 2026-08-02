from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from core.strategy_authoring import (
    legacy_farm_source_to_strategy_source,
)
from core.strategy_profiles import (
    StrategyProfileConflictError,
    StrategyProfileError,
    StrategyProfileStore,
    load_published_strategy_plan,
)
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "config" / "strategies"


def _yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _source(
    identifier: str = "history_strategy",
    *,
    damage: str = "1e-19",
) -> dict:
    source = legacy_farm_source_to_strategy_source(
        _yaml(STRATEGIES / "farm_t18.source.yaml"),
        display_name="History Strategy",
    )
    source["id"] = identifier
    source["display_name"] = "History Strategy"
    source["version"] = 1
    source["settings"]["damage_slider"] = {
        "policy": "enforce",
        "value": damage,
    }
    return source


def _write_schema_one(profile_directory: Path) -> Path:
    profile_directory.mkdir(parents=True, exist_ok=True)
    source = _yaml(STRATEGIES / "farm_t18.source.yaml")
    source["meta"] = {**source["meta"], "name": "legacy_history", "version": 1}
    plan = build_strategy_yaml(source)
    publication = {
        "schema_version": 1,
        "id": "legacy_history",
        "display_name": "Legacy History",
        "published_at": "2026-08-02T09:00:00-07:00",
        "source_fingerprint": _fingerprint(source),
        "plan_fingerprint": _fingerprint(plan),
        "source": source,
        "plan": plan,
    }
    path = profile_directory / "legacy_history.profile.yaml"
    path.write_text(yaml.safe_dump(publication, sort_keys=False), encoding="utf-8")
    return path


def _tree_bytes(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.name.endswith(".lock")
    }


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_publication_history_is_immutable_complete_and_restart_monotonic(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source())
    first_revision = tmp_path / "history" / "history_strategy.strategy.1.yaml"
    first_bytes = first_revision.read_bytes()
    second = store.publish(
        _source(damage="1e-18"),
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )

    reopened = StrategyProfileStore(profile_directory=tmp_path)
    third = reopened.publish(
        _source(damage="1e-17"),
        expected_source_fingerprint=second["profile"]["source_fingerprint"],
    )
    history = reopened.history_catalog()["lineages"][0]

    assert third["profile"]["version"] == 3
    assert first_revision.read_bytes() == first_bytes
    assert [item["logical_version"] for item in history["revisions"]] == [3, 2, 1]
    assert history["revisions"][0]["active_latest"] is True
    assert all(item["current_validation_valid"] for item in history["revisions"])
    retained = _yaml(first_revision)
    for field in (
        "source",
        "base_snapshot",
        "resolution",
        "plan",
        "source_fingerprint",
        "base_fingerprint",
        "resolution_fingerprint",
        "plan_fingerprint",
        "logical_version",
        "published_at",
        "audit_identity",
        "publication_origin",
    ):
        assert field in retained
    latest = _yaml(tmp_path / "history_strategy.profile.yaml")
    assert history["revisions"][0]["publication_fingerprint"] == _fingerprint(latest)
    assert history["revisions"][0]["source_fingerprint"] == latest[
        "source_fingerprint"
    ]
    assert not _contains_key(history, "plan")


@pytest.mark.parametrize("schema", (1, 2))
def test_existing_latest_adoption_is_exact_idempotent_and_does_not_rewrite(
    tmp_path,
    schema,
):
    if schema == 1:
        latest_path = _write_schema_one(tmp_path)
        identifier = "legacy_history"
    else:
        seeded = StrategyProfileStore(profile_directory=tmp_path)
        seeded.publish(_source())
        latest_path = tmp_path / "history_strategy.profile.yaml"
        identifier = "history_strategy"
        (tmp_path / "history" / "history_strategy.strategy.1.yaml").unlink()
    exact_latest = latest_path.read_bytes()

    first = StrategyProfileStore(profile_directory=tmp_path).history_catalog(identifier)
    first_revision = tmp_path / "history" / f"{identifier}.strategy.1.yaml"
    exact_revision = first_revision.read_bytes()
    second = StrategyProfileStore(profile_directory=tmp_path).history_catalog(identifier)

    assert latest_path.read_bytes() == exact_latest
    assert first_revision.read_bytes() == exact_revision
    assert len(first["lineages"][0]["revisions"]) == 1
    assert second == first
    assert first["lineages"][0]["revisions"][0][
        "publication_origin"
    ] == "conservative_adoption"
    assert load_published_strategy_plan(identifier, tmp_path) is not None


def test_existing_retirement_archive_is_adopted_without_mutating_evidence(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    published = store.publish(_source())
    retirement = store.retire_strategy(
        "history_strategy",
        expected_source_fingerprint=published["profile"]["source_fingerprint"],
    )
    archive = tmp_path / "retired" / retirement["archive_name"]
    archive_bytes = archive.read_bytes()
    (tmp_path / "history" / "history_strategy.strategy.1.yaml").unlink()

    reopened = StrategyProfileStore(profile_directory=tmp_path)
    lineage = reopened.history_catalog("history_strategy")["lineages"][0]

    assert archive.read_bytes() == archive_bytes
    assert lineage["retired"] is True
    assert lineage["revisions"][0]["publication_origin"] == "conservative_adoption"
    assert lineage["revisions"][0]["logical_version"] == 1


def test_retirement_retains_lineage_blocks_id_reset_and_restores_as_new(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source())
    store.retire_strategy(
        "history_strategy",
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )
    lineage = store.history_catalog("history_strategy")["lineages"][0]
    selected = lineage["revisions"][0]

    assert lineage["retired"] is True
    assert selected["status"] == "retired_latest"
    with pytest.raises(StrategyProfileConflictError, match="retired immutable lineage"):
        store.publish(_source())

    before_preview = _tree_bytes(tmp_path)
    preview = store.compare_strategy_revision(
        "history_strategy",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=None,
        require_optimistic_state=True,
    )
    assert _tree_bytes(tmp_path) == before_preview
    assert preview["next_logical_version"] == 2
    assert preview["publication_activates_strategy"] is False
    restored = store.publish_restore_strategy(
        "history_strategy",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=None,
        reviewed_restore_fingerprint=preview["reviewed_restore_fingerprint"],
    )

    assert restored["profile"]["version"] == 2
    assert restored["restored"] is True
    assert store.history_catalog("history_strategy")["lineages"][0][
        "active_latest"
    ] is True
    assert len(store.history_catalog("history_strategy")["lineages"][0]["revisions"]) == 2


def test_restore_uses_embedded_base_and_detects_stale_latest_or_revision(tmp_path):
    profile_directory = tmp_path / "profiles"
    store = StrategyProfileStore(profile_directory=profile_directory)
    explicit = _source("pinned_history")
    base = store.publish_base(
        {
            "id": "history_base",
            "display_name": "History Base",
            "family": "farm",
            "settings": explicit["settings"],
        }
    )
    sparse = {
        "schema_version": 2,
        "kind": "strategy",
        "id": "pinned_history",
        "display_name": "Pinned History",
        "family": "farm",
        "tier": 18,
        "version": 1,
        "base": {"id": "history_base", "revision": 1},
        "settings": {},
    }
    first = store.publish(sparse)
    second_source = copy.deepcopy(sparse)
    second_source["settings"] = {
        "damage_slider": {"policy": "enforce", "value": "1e-18"}
    }
    second = store.publish(
        second_source,
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )
    selected = store.history_catalog("pinned_history")["lineages"][0][
        "revisions"
    ][1]
    (profile_directory / "bases" / "history_base.base.1.yaml").unlink()

    preview = store.compare_strategy_revision(
        "pinned_history",
        1,
        expected_revision_fingerprint=selected["revision_fingerprint"],
        expected_latest_source_fingerprint=second["profile"]["source_fingerprint"],
        require_optimistic_state=True,
    )
    assert preview["valid"] is True
    assert preview["comparison"]["base_snapshot_changes"]["after_reference"] == {
        "id": "history_base",
        "revision": 1,
    }
    with pytest.raises(StrategyProfileConflictError, match="historical revision"):
        store.publish_restore_strategy(
            "pinned_history",
            1,
            expected_revision_fingerprint="stale",
            expected_latest_source_fingerprint=second["profile"][
                "source_fingerprint"
            ],
            reviewed_restore_fingerprint=preview["reviewed_restore_fingerprint"],
        )
    with pytest.raises(StrategyProfileConflictError, match="latest state changed"):
        store.publish_restore_strategy(
            "pinned_history",
            1,
            expected_revision_fingerprint=selected["revision_fingerprint"],
            expected_latest_source_fingerprint="stale",
            reviewed_restore_fingerprint=preview["reviewed_restore_fingerprint"],
        )
    assert base["snapshot"]["id"] == "history_base"


def test_history_corruption_and_symlink_are_reported_without_hiding_safe_latest(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    store.publish(_source())
    revision_path = tmp_path / "history" / "history_strategy.strategy.1.yaml"
    revision_path.write_text("kind: corrupt\n", encoding="utf-8")

    history = store.history_catalog()
    assert history["lineages"] == []
    assert any("Unsupported Strategy revision schema" in item["error"] for item in history["errors"])
    assert load_published_strategy_plan("history_strategy", tmp_path) is not None
    assert any(item["id"] == "history_strategy" for item in store.catalog()["items"])
    with pytest.raises(StrategyProfileConflictError, match="ambiguous or corrupt"):
        store.publish(
            _source(damage="1e-18"),
            expected_source_fingerprint=_yaml(
                tmp_path / "history_strategy.profile.yaml"
            )["source_fingerprint"],
        )

    revision_path.unlink()
    outside = tmp_path / "outside.yaml"
    outside.write_text("kind: evidence\n", encoding="utf-8")
    revision_path.symlink_to(outside)
    history = StrategyProfileStore(profile_directory=tmp_path).history_catalog()
    assert any("symbolic-link" in item["error"] for item in history["errors"])


def test_duplicate_or_misnumbered_history_evidence_is_rejected(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source())
    source_path = tmp_path / "history" / "history_strategy.strategy.1.yaml"
    duplicate_path = tmp_path / "history" / "history_strategy.strategy.2.yaml"
    duplicate_path.write_bytes(source_path.read_bytes())

    history = StrategyProfileStore(profile_directory=tmp_path).history_catalog()

    assert any(
        "logical version does not match" in item["error"]
        for item in history["errors"]
    )
    assert load_published_strategy_plan("history_strategy", tmp_path) is not None
    with pytest.raises(StrategyProfileConflictError, match="ambiguous or corrupt"):
        StrategyProfileStore(profile_directory=tmp_path).publish(
            _source(damage="1e-18"),
            expected_source_fingerprint=first["profile"]["source_fingerprint"],
        )


def test_hidden_or_unknown_history_and_transaction_artifacts_are_reported(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    store.publish(_source())
    outside = tmp_path / "outside-artifact.yaml"
    outside.write_text("kind: evidence\n", encoding="utf-8")
    (tmp_path / "history" / ".hidden.yaml").symlink_to(outside)
    (tmp_path / "transactions" / ".unknown.yaml").symlink_to(outside)

    history = StrategyProfileStore(profile_directory=tmp_path).history_catalog()

    assert load_published_strategy_plan("history_strategy", tmp_path) is not None
    assert any(
        item["id"] == "history/.hidden.yaml"
        and "symbolic-link" in item["error"]
        for item in history["errors"]
    )
    assert any(
        item["id"] == "transactions/.unknown.yaml"
        and "symbolic-link" in item["error"]
        for item in history["errors"]
    )


@pytest.mark.parametrize(
    "transition",
    (
        "transaction_record_durable",
        "revision_stage_durable",
        "latest_stage_durable",
        "previous_stage_durable",
        "revision_linked",
        "history_fsynced",
        "latest_replaced",
    ),
)
def test_failed_publication_transition_keeps_former_latest_and_no_phantom(
    tmp_path,
    transition,
):
    profile_directory = tmp_path / transition
    baseline = StrategyProfileStore(profile_directory=profile_directory).publish(
        _source()
    )
    former_latest = (profile_directory / "history_strategy.profile.yaml").read_bytes()

    def fail(selected: str) -> None:
        if selected == transition:
            raise OSError(f"interrupted at {transition}")

    failing = StrategyProfileStore(
        profile_directory=profile_directory,
        transaction_fault_hook=fail,
    )
    with pytest.raises(StrategyProfileError, match="Unable to publish"):
        failing.publish(
            _source(damage="1e-18"),
            expected_source_fingerprint=baseline["profile"]["source_fingerprint"],
        )

    assert (profile_directory / "history_strategy.profile.yaml").read_bytes() == former_latest
    lineage = StrategyProfileStore(
        profile_directory=profile_directory
    ).history_catalog()["lineages"][0]
    assert [item["logical_version"] for item in lineage["revisions"]] == [1]
    assert not list((profile_directory / "transactions").glob("*.yaml"))


@pytest.mark.parametrize(
    "transition",
    ("latest_directory_fsynced", "transaction_cleaned"),
)
def test_interruption_after_durable_commit_remains_one_exact_publication(
    tmp_path,
    transition,
):
    first = StrategyProfileStore(profile_directory=tmp_path).publish(_source())

    def fail(selected: str) -> None:
        if selected == transition:
            raise OSError(f"interrupted at {transition}")

    committed = StrategyProfileStore(
        profile_directory=tmp_path,
        transaction_fault_hook=fail,
    ).publish(
        _source(damage="1e-18"),
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )
    lineage = StrategyProfileStore(profile_directory=tmp_path).history_catalog()[
        "lineages"
    ][0]

    assert committed["profile"]["version"] == 2
    assert [item["logical_version"] for item in lineage["revisions"]] == [2, 1]
    assert lineage["revisions"][0]["active_latest"] is True


def test_post_commit_cleanup_failure_returns_success_and_recovers_once(tmp_path):
    first = StrategyProfileStore(profile_directory=tmp_path).publish(_source())
    committing = StrategyProfileStore(profile_directory=tmp_path)

    def fail_cleanup(*paths: Path) -> None:
        raise OSError("simulated cleanup failure")

    committing._cleanup_transaction = fail_cleanup  # type: ignore[method-assign]
    second = committing.publish(
        _source(damage="1e-18"),
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )

    assert second["profile"]["version"] == 2
    assert (tmp_path / "transactions" / "history_strategy.publication.yaml").is_file()
    reopened = StrategyProfileStore(profile_directory=tmp_path)
    lineage = reopened.history_catalog()["lineages"][0]
    assert [item["logical_version"] for item in lineage["revisions"]] == [2, 1]
    assert lineage["revisions"][0]["active_latest"] is True
    assert list((tmp_path / "transactions").iterdir()) == []


def test_staged_transaction_recovers_deterministically_and_retry_is_idempotent(
    tmp_path,
):
    first_store = StrategyProfileStore(profile_directory=tmp_path)
    first = first_store.publish(_source())
    former_latest = (tmp_path / "history_strategy.profile.yaml").read_bytes()

    def crash_after_revision(transition: str) -> None:
        if transition == "revision_linked":
            raise OSError("simulated crash")

    interrupted = StrategyProfileStore(
        profile_directory=tmp_path,
        transaction_fault_hook=crash_after_revision,
    )
    interrupted._rollback_publication_transaction = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        OSError("process exited before rollback")
    )
    with pytest.raises(StrategyProfileError, match="could not be rolled back"):
        interrupted.publish(
            _source(damage="1e-18"),
            expected_source_fingerprint=first["profile"]["source_fingerprint"],
        )
    assert (tmp_path / "history_strategy.profile.yaml").read_bytes() == former_latest

    reopened = StrategyProfileStore(profile_directory=tmp_path)
    lineage = reopened.history_catalog()["lineages"][0]
    assert [item["logical_version"] for item in lineage["revisions"]] == [2, 1]
    assert lineage["revisions"][0]["active_latest"] is True
    recovered_source_fingerprint = lineage["latest_source_fingerprint"]
    with pytest.raises(StrategyProfileConflictError, match="changed after"):
        reopened.publish(
            _source(damage="1e-18"),
            expected_source_fingerprint=first["profile"]["source_fingerprint"],
        )
    assert reopened.history_catalog()["lineages"][0][
        "latest_source_fingerprint"
    ] == recovered_source_fingerprint


def test_semantic_comparison_reports_directives_effective_base_plan_and_metadata(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source())
    changed = _source(damage="1e-18")
    changed["display_name"] = "Renamed History Strategy"
    second = store.publish(
        changed,
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )

    comparison = store.compare_strategy_revision(
        "history_strategy",
        1,
    )["comparison"]

    assert comparison["source_changes"]["changed"]
    assert comparison["effective_changes"]["changed"]
    assert comparison["local_override_changes"]["change_count"] >= 1
    assert comparison["explicit_ignore_changes"]["change_count"] == 0
    assert comparison["generated_plan_changes"]["changed"] is True
    assert comparison["generated_plan_changes"]["before_rule_count"] == comparison[
        "generated_plan_changes"
    ]["after_rule_count"]
    assert comparison["metadata_only"] is False
    assert second["profile"]["version"] == 2


def test_comparison_reports_current_builder_errors_but_restore_fails_closed(
    tmp_path,
    monkeypatch,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    first = store.publish(_source())
    second = store.publish(
        _source(damage="1e-18"),
        expected_source_fingerprint=first["profile"]["source_fingerprint"],
    )
    selected = store.history_catalog()["lineages"][0]["revisions"][1]
    before = _tree_bytes(tmp_path)

    def reject_current_builder(source: object) -> dict:
        raise ValueError("current builder rejected retained intent")

    monkeypatch.setattr(
        "core.strategy_profiles.build_strategy_yaml",
        reject_current_builder,
    )
    catalog = store.history_catalog()
    comparison = store.compare_strategy_revision(
        "history_strategy",
        1,
    )

    assert catalog["lineages"][0]["revisions"][0][
        "current_validation_valid"
    ] is False
    assert comparison["valid"] is False
    assert comparison["comparison"]["validation"]["valid"] is False
    assert {
        item["code"]
        for item in comparison["comparison"]["validation"]["errors"]
    } == {"current_latest_validation", "historical_revision_validation"}
    assert all(
        "current builder rejected" in item["message"]
        for item in comparison["comparison"]["validation"]["errors"]
    )
    with pytest.raises(StrategyProfileError, match="current builder rejected"):
        store.compare_strategy_revision(
            "history_strategy",
            1,
            expected_revision_fingerprint=selected["revision_fingerprint"],
            expected_latest_source_fingerprint=second["profile"][
                "source_fingerprint"
            ],
            require_optimistic_state=True,
        )
    assert _tree_bytes(tmp_path) == before
