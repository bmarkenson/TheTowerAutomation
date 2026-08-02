from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading

import pytest

from automation.strategies import get_strategy
from core.automation_process import SystemdAutomationManager
from core.control_directives import ControlDirectiveStore
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
from core.gate_decisions import (
    merge_profile_skip_waivers,
    startup_gate_check_catalog,
)
from core.perk_configuration import FARM_AUTO_PICK_ORDER, FARM_PERK_BANS
from core.strategy_profiles import (
    StrategyProfileConflictError,
    StrategyProfileError,
    StrategyProfileStore,
    configurable_strategy_ids,
    load_published_strategy_plan,
)
from tools.control_surface_server import ControlSurfaceHTTPServer, STATIC_DIR


def _draft(
    identifier: str = "farm_t19_custom",
    *,
    damage_value: str = "1e-19",
    skipped_checks: tuple[str, ...] = (),
    perk_bans: tuple[str, ...] = FARM_PERK_BANS,
    perk_auto_pick_order: tuple[str, ...] = FARM_AUTO_PICK_ORDER,
) -> dict[str, object]:
    return {
        "id": identifier,
        "display_name": "Farm T19 Custom",
        "tier": 19,
        "setup": {
            "skipped_checks": list(skipped_checks),
            "settings": {
                "perk_bans": list(perk_bans),
                "perk_auto_pick_order": list(perk_auto_pick_order),
            },
        },
        "loadout": {
            "modules": {"mode": "enforce", "preset": "farm_standard"},
            "damage_slider": {
                "mode": "enforce",
                "value": damage_value,
            },
            "orb_distance": {
                "mode": "enforce",
                "preset": "farm_min_range",
            },
            "target_priority": {
                "mode": "enforce",
                "preset": "farm_t19",
            },
        },
    }


def test_strategy_profile_catalog_exposes_bundled_profiles_and_presets(
    tmp_path,
):
    catalog = StrategyProfileStore(profile_directory=tmp_path).catalog()

    assert catalog["schema_version"] == 1
    assert catalog["policy_modes"] == ["enforce", "observe", "preserve"]
    assert [item["id"] for item in catalog["setup_checks"]] == [
        "auto_pick_perks",
        "perk_bans",
        "perk_auto_pick_order",
    ]
    assert any(item["id"] == "coin_tradeoff" for item in catalog["perks"])
    assert [item["id"] for item in catalog["items"]] == [
        "farm_t18",
        "farm_t19",
        "tournament",
        "none",
    ]
    assert catalog["items"][0]["loadout"]["modules"] == {
        "mode": "enforce",
        "preset": "farm_standard",
    }
    assert catalog["items"][0]["setup"]["settings"]["perk_bans"] == list(
        FARM_PERK_BANS
    )
    assert [item["id"] for item in catalog["presets"]["modules"]] == [
        "farm_standard",
        "tournament_standard",
    ]


def test_strategy_profile_publish_is_atomic_loadable_and_versioned(
    tmp_path,
    monkeypatch,
):
    store = StrategyProfileStore(profile_directory=tmp_path)

    validated = store.validate(_draft())
    assert validated["valid"]
    assert validated["profile"]["version"] == 1
    assert validated["resolved_configuration"]["tier"] == 19
    assert validated["rule_count"] > 0

    published = store.publish(_draft())
    path = tmp_path / "farm_t19_custom.profile.yaml"
    assert path.is_file()
    assert not list(tmp_path.glob("*.tmp"))
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert published["published"] is True
    assert published["profile"]["version"] == 1
    assert "farm_t19_custom" in configurable_strategy_ids(tmp_path)

    plan = load_published_strategy_plan("farm_t19_custom", tmp_path)
    assert plan is not None
    assert plan["meta"]["name"] == "farm_t19_custom"
    assert plan["run_configuration"]["tier"] == 19

    monkeypatch.setenv("THETOWER_STRATEGY_PROFILE_DIR", str(tmp_path))
    strategy = get_strategy("farm_t19_custom")
    assert strategy is not None
    assert strategy.name == "farm_t19_custom"
    assert strategy.run_configuration()["tier"] == 19

    updated_draft = _draft(damage_value="1e-18")
    updated = store.publish(
        updated_draft,
        expected_source_fingerprint=published["profile"][
            "source_fingerprint"
        ],
    )
    assert updated["profile"]["version"] == 2
    assert updated["profile"]["loadout"]["damage_slider"]["value"] == (
        "1E-18%"
    )


def test_strategy_profile_publish_requires_current_revision(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    published = store.publish(_draft())

    with pytest.raises(StrategyProfileConflictError, match="changed after"):
        store.publish(
            _draft(damage_value="1e-18"),
            expected_source_fingerprint="stale",
        )

    current = store.catalog()["items"][-1]
    assert current["source_fingerprint"] == published["profile"][
        "source_fingerprint"
    ]
    assert current["version"] == 1


def test_strategy_profile_retirement_is_recoverable_and_stale_safe(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    published = store.publish(_draft())
    active_path = tmp_path / "farm_t19_custom.profile.yaml"
    exact_publication = active_path.read_bytes()

    with pytest.raises(StrategyProfileConflictError, match="changed after"):
        store.retire_strategy(
            "farm_t19_custom",
            expected_source_fingerprint="stale",
        )
    assert active_path.read_bytes() == exact_publication
    assert not (tmp_path / "retired").exists()

    retirement = store.retire_strategy(
        "farm_t19_custom",
        expected_source_fingerprint=published["profile"][
            "source_fingerprint"
        ],
    )

    assert retirement["id"] == "farm_t19_custom"
    assert retirement["display_name"] == "Farm T19 Custom"
    assert retirement["version"] == 1
    assert retirement["recoverable"] is True
    archive_path = tmp_path / "retired" / retirement["archive_name"]
    assert archive_path.read_bytes() == exact_publication
    assert not active_path.exists()
    assert "farm_t19_custom" not in configurable_strategy_ids(tmp_path)
    assert load_published_strategy_plan("farm_t19_custom", tmp_path) is None
    assert "farm_t19_custom" not in {
        item["id"] for item in store.catalog()["items"]
    }

    with pytest.raises(StrategyProfileConflictError, match="no longer exists"):
        store.retire_strategy(
            "farm_t19_custom",
            expected_source_fingerprint=retirement["source_fingerprint"],
        )
    with pytest.raises(StrategyProfileError, match="bundled or reserved"):
        store.retire_strategy(
            "farm_t18",
            expected_source_fingerprint="unused",
        )


def test_strategy_profile_retirement_rejects_archive_symlink(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    published = store.publish(_draft())
    active_path = tmp_path / "farm_t19_custom.profile.yaml"
    exact_publication = active_path.read_bytes()
    outside = tmp_path / "outside-retired"
    outside.mkdir()
    (tmp_path / "retired").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StrategyProfileConflictError, match="not a regular"):
        store.retire_strategy(
            "farm_t19_custom",
            expected_source_fingerprint=published["profile"][
                "source_fingerprint"
            ],
        )

    assert active_path.read_bytes() == exact_publication
    assert list(outside.iterdir()) == []


def test_strategy_profile_edits_complete_setup_and_persists_permanent_skips(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)
    draft = _draft(
        skipped_checks=(
            "auto_pick_perks",
            "perk_bans",
            "perk_auto_pick_order",
        ),
        perk_bans=("cash_tradeoff", "interest"),
        perk_auto_pick_order=("coin_tradeoff", "game_speed", "damage"),
    )
    # The complete settings object can already carry non-perk edits even
    # before each setting has a specialized native control.
    draft["setup"]["settings"]["card_recharge_modes"] = {
        "Demon Mode": "ready_after_recharge",
        "Nuke": "ready_after_recharge",
    }

    validated = store.validate(draft)

    setup = validated["profile"]["setup"]
    assert setup["skipped_checks"] == [
        "auto_pick_perks",
        "perk_bans",
        "perk_auto_pick_order",
    ]
    assert setup["settings"]["perk_bans"] == ["cash_tradeoff", "interest"]
    assert setup["settings"]["perk_auto_pick_order"] == [
        "coin_tradeoff",
        "game_speed",
        "damage",
    ]
    assert setup["settings"]["card_recharge_modes"]["Demon Mode"] == (
        "ready_after_recharge"
    )
    requirements = validated["plan"]["session_preflight"]["requirements"]
    assert requirements["profile_skips"] == setup["skipped_checks"]
    assert validated["resolved_configuration"]["skipped_checks"] == (
        setup["skipped_checks"]
    )

    published = store.publish(draft)
    catalog_item = store.catalog()["items"][-1]
    assert catalog_item["setup"] == published["profile"]["setup"]

    gate_ids = {
        item["id"] for item in startup_gate_check_catalog(requirements)
    }
    assert not {
        "auto_pick_perks",
        "perk_bans",
        "perk_auto_pick_order",
    } & gate_ids
    waivers = merge_profile_skip_waivers(
        requirements,
        {"bots_preset": {"source": "test"}},
    )
    assert waivers["perk_bans"]["scope"] == "every_run"
    assert waivers["bots_preset"] == {"source": "test"}


def test_strategy_profile_rejects_invalid_setup_edits(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    invalid_skip = _draft(skipped_checks=("damage_slider",))
    with pytest.raises(StrategyProfileError, match="unsupported checks"):
        store.validate(invalid_skip)

    duplicate_ban = _draft(perk_bans=("interest", "interest"))
    with pytest.raises(StrategyProfileError, match="cannot repeat"):
        store.validate(duplicate_ban)

    no_bans = store.validate(_draft(perk_bans=()))
    assert no_bans["profile"]["setup"]["settings"]["perk_bans"] == []


def test_strategy_profile_rejects_bundled_names_and_inconsistent_policies(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path)

    with pytest.raises(StrategyProfileError, match="reserved strategy name"):
        store.validate(_draft("farm_t19"))

    invalid = _draft()
    invalid["loadout"]["modules"] = {
        "mode": "preserve",
        "preset": "farm_standard",
    }
    with pytest.raises(StrategyProfileError, match="must not supply a preset"):
        store.validate(invalid)


def test_control_surface_validates_and_publishes_profile_without_activating_it(
    tmp_path,
):
    profile_dir = tmp_path / "profiles"
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=profile_dir,
    )

    validated = service.apply_strategy_profile(
        {"action": "validate", "profile": _draft()}
    )
    assert validated["action"] == "validate"
    assert validated["valid"] is True
    assert service.control_store.status()["strategy"] is None

    published = service.apply_strategy_profile(
        {"action": "publish", "profile": _draft()}
    )
    assert published["action"] == "publish"
    assert published["profile"]["id"] == "farm_t19_custom"
    assert published["catalog"]["items"][-1]["editable"] is True
    assert service.control_store.status()["strategy"] is None
    assert "Published custom strategy profile farm_t19_custom version 1" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")

    directive = ControlDirectiveStore(
        tmp_path / "logs" / "custom_ctl.json",
        strategy_profile_dir=profile_dir,
    ).set_strategy("farm_t19_custom", source="test")
    assert directive["strategy"] == "farm_t19_custom"


def test_control_surface_maps_profile_conflict_to_http_conflict(tmp_path):
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=tmp_path / "profiles",
    )
    service.apply_strategy_profile({"action": "publish", "profile": _draft()})

    with pytest.raises(ControlSurfaceRequestError) as error:
        service.apply_strategy_profile(
            {
                "action": "publish",
                "profile": _draft(damage_value="1e-18"),
                "expected_source_fingerprint": "stale",
            }
        )
    assert error.value.status == 409


def test_systemd_manager_accepts_published_custom_profile(tmp_path):
    profile_dir = tmp_path / "profiles"
    StrategyProfileStore(profile_directory=profile_dir).publish(_draft())
    environment_file = tmp_path / "automation.env"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "LoadState=loaded\nActiveState=inactive\nSubState=dead\n"
            "UnitFileState=enabled\nMainPID=0\nExecMainStatus=0\n"
            f"EnvironmentFiles={environment_file} (ignore_errors=yes)\n",
            "",
        )

    manager = SystemdAutomationManager(
        adb_environment_file=environment_file,
        strategy_profile_dir=profile_dir,
        runner=runner,
    )

    status = manager.set_strategy("farm_t19_custom")

    assert status["strategy"] == "farm_t19_custom"
    assert "farm_t19_custom" in status["strategy_options"]
    assert "THETOWER_STRATEGY=farm_t19_custom" in environment_file.read_text(
        encoding="utf-8"
    )


def test_strategy_profile_http_api_does_not_return_expanded_plan(tmp_path):
    service = ControlSurfaceService(
        repository_root=tmp_path,
        strategy_profile_dir=tmp_path / "profiles",
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
        timeout=3,
    )
    try:
        connection.request("GET", "/api/v1/strategy-profiles")
        response = connection.getresponse()
        catalog = json.loads(response.read())
        assert response.status == 200
        assert catalog["items"][0]["id"] == "farm_t18"

        body = json.dumps({"action": "validate", "profile": _draft()})
        connection.request(
            "POST",
            "/api/v1/strategy-profiles",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode("utf-8"))),
            },
        )
        response = connection.getresponse()
        validated = json.loads(response.read())
        assert response.status == 200
        assert validated["valid"] is True
        assert "plan" not in validated
        assert "source" not in validated
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_tampered_publication_is_not_selectable(tmp_path):
    store = StrategyProfileStore(profile_directory=tmp_path)
    store.publish(_draft())
    path = tmp_path / "farm_t19_custom.profile.yaml"
    raw = path.read_text(encoding="utf-8").replace(
        "plan_fingerprint:",
        "plan_fingerprint: tampered #",
        1,
    )
    path.write_text(raw, encoding="utf-8")

    assert "farm_t19_custom" not in configurable_strategy_ids(tmp_path)
    assert load_published_strategy_plan("farm_t19_custom", tmp_path) is None
    catalog = store.catalog()
    assert catalog["errors"][0]["id"] == "farm_t19_custom"
