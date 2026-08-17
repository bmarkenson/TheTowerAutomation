from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.action_authority import (
    AuthorityHold,
    AuthorityHoldState,
    AuxiliaryCollector,
    RuntimeActionAuthority,
    RuntimeActionAuthorityPublisher,
)
from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS,
    VALID_GAME_SPEED_TARGETS,
)
from core.development_adb_input import validate_active_lease_status
from core.control_surface import (
    CONTROL_SURFACE_CAPABILITIES,
    CONTROL_SURFACE_REVISION,
    ControlSurfaceRequestError,
    ControlSurfaceService,
)
from core.exclusive_validation import (
    exclusive_validation_definition_for_strategy,
)
from core.gate_decisions import build_gate_decision_options
from core.player_save_mapping_staged_candidate import SaveMappingIntegrationError
from tools.control_surface_server import (
    ControlSurfaceHTTPServer,
    STATIC_DIR,
    _save_mapping_reconciliation_loop,
    main,
)


ACTIVE_BATTLE_IDENTITY = "a" * 64


def _service(root: Path, *, stale_after_seconds: int = 180) -> ControlSurfaceService:
    return ControlSurfaceService(
        repository_root=root,
        stale_after_seconds=stale_after_seconds,
    )


def _legacy_terminal_attestation_status(identity_fingerprint: str):
    owner = {
        "runtime_id": "runtime-current",
        "pid": 1234,
        "adb_target": "localhost:5555",
        "target_generation": 2,
    }
    observation = {
        "observation_id": "runtime-current:42",
        "observed_at": "2026-08-16T19:00:00+00:00",
        "primary_state": "GAME_OVER",
        "game_state": "game_over",
        "activity_scope_run_id": "scope-current",
        "freshness": "fresh",
        "available": True,
    }
    evidence = {**observation, **owner}
    return {
        "healthy": True,
        "control": {
            "state": "PAUSED",
            "resume_at": None,
            "mode": "NEXT_BATTLE",
            "strategy": "farm_t19",
            "process_restart_handoff": {
                "status": "failed",
                "expected_active_round_identity_fingerprint": (
                    identity_fingerprint
                ),
                "source_evidence": {
                    "game_state": "active_battle",
                    "active_round_identity_fingerprint": (
                        identity_fingerprint
                    ),
                    "adb_target": "localhost:5555",
                    "target_generation": 3,
                    "activity_scope_run_id": "scope-current",
                },
            },
        },
        "acknowledgements": {
            "state": {
                "value": "PAUSED",
                "acknowledges_current": True,
            },
            "mode": {
                "value": "NEXT_BATTLE",
                "acknowledges_current": True,
            },
            "strategy": {
                "value": "farm_t19",
                "acknowledges_current": True,
            },
        },
        "control_model": {
            "action_authority": {"effective": "paused"},
            "observation": observation,
            "workflow_evidence": evidence,
            "strategy_scope": {"startup_default": "farm_t19"},
        },
        "strategy_action_gate": {
            "available": True,
            "stale": False,
            "owner_matches_exact_runtime": True,
            "global_pause": True,
            "owner": owner,
        },
        "runtime": {
            "instances": [
                {
                    "active": True,
                    "runtime_id": "runtime-current",
                    "pid": 1234,
                    "target": "localhost:5555",
                    "target_generation": 2,
                }
            ]
        },
        "process_service": {
            "active": True,
            "main_pid": 1234,
            "adb_target": "localhost:5555",
            "strategy": "farm_t19",
        },
        "adb_connection": {
            "connected": True,
            "target": "localhost:5555",
        },
        "current_run": {"run_id": "scope-current"},
    }


def _write_battle(root: Path, battle_id: str = "Battle20260719T101126-0700") -> Path:
    record = {
        "schema_version": 2,
        "battle_id": battle_id,
        "captured_at": "2026-07-19T10:11:26-07:00",
        "strategy": "farm_t18",
        "battle_type": "farm",
        "battle_type_analysis": {
            "type": "farm",
            "label": "Farm",
            "confidence": "high",
        },
        "run_configuration": {"profile": "farm"},
        "more_stats": {
            "sections": [
                {
                    "name": "Battle Report",
                    "key": "battle_report",
                    "rows": [
                        {"key": "tier", "value_raw": "18", "value": 18},
                        {"key": "wave", "value_raw": "520", "value": 520},
                        {"key": "real_time", "value_raw": "6m 16s", "value": 376},
                        {"key": "game_time", "value_raw": "26m 35s", "value": 1595},
                        {"key": "coins_earned", "value_raw": "1.2T"},
                        {"key": "coins_per_hour", "value_raw": "11.5T"},
                        {"key": "cells_earned", "value_raw": "797", "value": 797},
                        {"key": "cells_per_hour", "value_raw": "7.62K"},
                    ],
                }
            ]
        },
        "derived": {"effective_game_speed": 4.242},
        "quality": {"valid": True, "warnings": []},
    }
    path = root / "logs" / "battles" / f"{battle_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _ready_tournament_launch(service: ControlSurfaceService):
    store = service.control_store
    saved = store.set_strategy("tournament", source="test")
    definition = exclusive_validation_definition_for_strategy("tournament")
    assert definition is not None
    owner = {
        "runtime_id": "validation-runtime",
        "pid": 101,
        "adb_target": "localhost:5555",
    }
    claimed = store.claim_exclusive_validation(
        strategy_request_id=saved["strategy_request_id"],
        configuration_fingerprint=definition.configuration_fingerprint,
        owner=owner,
        timeout_seconds=definition.timeout_seconds,
    )
    running = store.mark_exclusive_validation_running(
        claimed["request_id"],
        owner=owner,
    )
    cleanup = store.begin_exclusive_validation_cleanup(
        running["request_id"],
        owner=owner,
        outcome="ready",
        reason="checks passed",
    )
    return store.finish_exclusive_validation(
        cleanup["request_id"],
        owner=owner,
        outcome="ready",
        reason="checks passed",
    )


def _fresh_runtime_lock(root: Path, *, state: str):
    now = datetime.now().astimezone().replace(microsecond=0)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = root / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"[STATUS {timestamp}] State={state} | Wave=— | Coins/min=—\n",
        encoding="utf-8",
    )
    lock_path = root / "logs" / "automation-localhost_5555.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "target": "localhost:5555",
                "started_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    lock_handle = lock_path.open("r", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock_handle


def _fresh_exact_runtime_lock(
    root: Path,
    *,
    runtime_id: str,
    target_generation: int,
    target: str = "localhost:5555",
):
    lock_path = root / "logs" / "automation-localhost_5555.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "runtime_id": runtime_id,
                "target": target,
                "target_generation": target_generation,
                "state": "held",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    lock_handle = lock_path.open("r", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock_handle


def _directive_acknowledgements(
    control: dict[str, object],
    *,
    acknowledged_at: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state": {
            "value": control["state"],
            "request_id": control["state_request_id"],
            "acknowledged_at": acknowledged_at,
        },
        "mode": {
            "value": control["mode"],
            "request_id": control["mode_request_id"],
            "acknowledged_at": acknowledged_at,
        },
        "game_speed_target": {
            "value": f"x{control['game_speed_target']:.1f}",
            "request_id": control["game_speed_target_request_id"],
            "acknowledged_at": acknowledged_at,
        },
        "adb_target": {
            "value": f"localhost:{control['adb_port']}",
            "request_id": control["adb_port_request_id"],
            "acknowledged_at": acknowledged_at,
        },
        "strategy": {
            "value": control["strategy"],
            "request_id": control["strategy_request_id"],
            "acknowledged_at": acknowledged_at,
        },
    }


def _publish_runtime_acknowledgements(
    root: Path,
    *,
    now: datetime,
    owner: dict[str, object],
    acknowledgements: dict[str, object],
    runtime_active: bool = True,
    strategy_scope: dict[str, object] | None = None,
    active_run_metrics: dict[str, object] | None = None,
    observation_identity: str | None = None,
    holds: tuple[AuthorityHoldState, ...] = (),
) -> None:
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="ack-scope",
        battle_identity=observation_identity,
        primary_state="RUNNING",
        holds=holds,
    )
    publisher = RuntimeActionAuthorityPublisher(
        root / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    )
    control_model: dict[str, object] = {
        "schema_version": 1,
        "observation": {
            "schema_version": 1,
            "observation_id": f"{owner['runtime_id']}:1",
            "observed_at": now.isoformat(timespec="seconds"),
            "primary_state": "RUNNING",
            "home_battle_control": "UNKNOWN",
            "game_state": "active_battle",
            "active_battle": True,
            "activity_scope_run_id": "ack-scope",
            "target_generation": owner.get("target_generation"),
        },
        "battle_lifecycle": {"active_battle_adopted": True},
        "strategy_scope": strategy_scope
        or {
            "startup_default": "farm_t18",
            "active_battle": "farm_t18",
            "pending_next_boundary": None,
            "pending_active_battle": None,
        },
    }
    if active_run_metrics is not None:
        control_model["active_run_metrics"] = active_run_metrics
    if observation_identity is not None:
        observation = control_model["observation"]
        assert isinstance(observation, dict)
        observation["active_round_identity_fingerprint"] = observation_identity
    assert publisher.publish(
        authority.snapshot(now=now.timestamp()),
        runtime_active=runtime_active,
        now=now.timestamp(),
        acknowledgements=acknowledgements,
        control_model=control_model,
    )


def _control_with_all_request_identities(root: Path) -> dict[str, object]:
    store = ControlDirectiveStore(root / "logs" / "automation_ctl.json")
    store.set_state("RUNNING", source="test")
    store.set_mode("WAIT", source="test")
    store.set_game_speed_target(4.5, source="test")
    store.set_adb_port(5555, source="test")
    store.set_strategy("farm_t18", source="test")
    return store.status()


def _write_current_run_scope(root: Path, *, run_id: str) -> None:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "activity_scope.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": "current_run",
                "run_id": run_id,
                "started_at": "2026-08-08T10:00:00-07:00",
                "source_file_id": "1:1",
                "start_offset": 0,
            }
        ),
        encoding="utf-8",
    )


def _current_perks_presentation() -> dict:
    return {
        "schema_version": 1,
        "status": "available",
        "reason": "",
        "source": "monitor_validated_player_save_perk_prefix",
        "order_semantics": "most_recent_selection_first",
        "captured_at": "2026-08-08T17:05:00+00:00",
        "saved_wave": 620,
        "picked_count": 5,
        "unique_count": 3,
        "items": [
            {
                "perk_key": "damage",
                "label": "Damage",
                "level": 1,
                "last_selected_wave": 580,
                "last_selected_sequence": 5,
            },
            {
                "perk_key": "perk_wave_requirement",
                "label": "Perk Wave Requirement",
                "level": 3,
                "last_selected_wave": 540,
                "last_selected_sequence": 4,
            },
            {
                "perk_key": "max_health",
                "label": "Max Health",
                "level": 1,
                "last_selected_wave": 100,
                "last_selected_sequence": 1,
            },
        ],
    }


def test_control_store_preserves_fields_and_resumes_only_matching_deadline(tmp_path):
    path = tmp_path / "automation_ctl.json"
    path.write_text(json.dumps({"mode": "WAIT", "custom": "keep"}), encoding="utf-8")
    store = ControlDirectiveStore(path)

    saved = store.set_state(
        "PAUSED",
        resume_at=1_300.0,
        source="control-surface",
    )
    assert saved["mode"] == "WAIT"
    assert saved["custom"] == "keep"
    assert saved["state"] == "PAUSED"
    assert saved["resume_at"] == 1_300.0
    assert saved["state_updated_at"]

    store.set_state("PAUSED", resume_at=1_600.0, source="control-surface")
    assert store.resume_expired_pause(expected_resume_at=1_300.0, now=1_700.0) is None
    assert store.read()["resume_at"] == 1_600.0

    resumed = store.resume_expired_pause(expected_resume_at=1_600.0, now=1_700.0)
    assert resumed is not None
    assert resumed["state"] == "RUNNING"
    assert "resume_at" not in resumed
    assert resumed["updated_by"] == "timed-pause-expiry"


def test_control_store_persists_strategy_without_changing_other_directives(tmp_path):
    path = tmp_path / "automation_ctl.json"
    path.write_text(
        json.dumps({"state": "PAUSED", "mode": "WAIT", "adb_port": 5565}),
        encoding="utf-8",
    )

    saved = ControlDirectiveStore(path).set_strategy(
        "tournament",
        source="test",
    )

    assert saved["state"] == "PAUSED"
    assert saved["mode"] == "WAIT"
    assert saved["adb_port"] == 5565
    assert saved["strategy"] == "tournament"
    assert saved["strategy_apply_mode"] == "next_boundary"
    assert saved["strategy_updated_at"]
    assert saved["strategy_request_id"]
    assert ControlDirectiveStore(path).status()["strategy"] == "tournament"
    assert (
        ControlDirectiveStore(path).status()["strategy_apply_mode"]
        == "next_boundary"
    )


def test_control_store_persists_game_speed_target_without_changing_run_state(
    tmp_path,
):
    path = tmp_path / "automation_ctl.json"
    path.write_text(
        json.dumps({"state": "RUNNING", "mode": "WAIT", "custom": "keep"}),
        encoding="utf-8",
    )
    store = ControlDirectiveStore(path)

    saved = store.set_game_speed_target(4.5, source="test")

    assert saved["state"] == "RUNNING"
    assert saved["mode"] == "WAIT"
    assert saved["custom"] == "keep"
    assert saved["game_speed_target"] == 4.5
    assert saved["game_speed_target_updated_at"]
    assert saved["game_speed_target_request_id"]
    assert store.status()["game_speed_target"] == 4.5
    with pytest.raises(ValueError, match="game-speed target"):
        store.set_game_speed_target(4.2)


def test_game_speed_targets_cover_half_steps_and_maximum_available(tmp_path):
    expected = tuple([step / 2 for step in range(13)] + [6.3])
    assert VALID_GAME_SPEED_TARGETS == expected

    store = ControlDirectiveStore(tmp_path / "automation_ctl.json")
    for target in expected:
        saved = store.set_game_speed_target(target)
        assert saved["game_speed_target"] == target


def test_control_store_persists_active_battle_strategy_adoption(tmp_path):
    path = tmp_path / "automation_ctl.json"
    battle_identity = "a" * 64

    saved = ControlDirectiveStore(path).set_strategy(
        "farm_t18",
        apply_mode="active_battle",
        active_battle_identity=battle_identity,
        source="test",
    )

    assert saved["strategy"] == "farm_t18"
    assert saved["strategy_apply_mode"] == "active_battle"
    assert saved["strategy_active_battle_identity"] == battle_identity
    assert ControlDirectiveStore(path).status()["strategy_apply_mode"] == (
        "active_battle"
    )
    assert ControlDirectiveStore(path).status()[
        "strategy_active_battle_identity"
    ] == battle_identity


def test_control_store_defers_only_exact_active_battle_strategy_request(tmp_path):
    path = tmp_path / "automation_ctl.json"
    store = ControlDirectiveStore(path)
    battle_identity = "a" * 64
    accepted = store.set_strategy(
        "farm_t18",
        apply_mode="active_battle",
        active_battle_identity=battle_identity,
        source="test",
    )
    request_id = accepted["strategy_request_id"]

    assert store.defer_strategy_request_to_next_boundary(
        "farm_t18",
        "stale-request",
        source="test-deferral",
    ) is None
    assert store.status()["strategy_apply_mode"] == "active_battle"

    deferred = store.defer_strategy_request_to_next_boundary(
        "farm_t18",
        request_id,
        source="test-deferral",
    )

    assert deferred is not None
    assert deferred["strategy_apply_mode"] == "next_boundary"
    assert "strategy_active_battle_identity" not in deferred
    assert deferred["strategy_request_id"] == request_id
    assert deferred["updated_by"] == "test-deferral"

    replacement = store.set_strategy(
        "farm_t19",
        apply_mode="active_battle",
        active_battle_identity="b" * 64,
        source="newer-request",
    )
    assert store.defer_strategy_request_to_next_boundary(
        "farm_t18",
        request_id,
    ) is None
    assert store.status()["strategy_request_id"] == replacement[
        "strategy_request_id"
    ]
    assert store.status()["strategy_apply_mode"] == "active_battle"


def test_control_store_rejects_unknown_strategy_apply_mode(tmp_path):
    with pytest.raises(ValueError, match="Strategy apply mode"):
        ControlDirectiveStore(tmp_path / "automation_ctl.json").set_strategy(
            "farm_t18",
            apply_mode="immediate",
        )


def test_control_store_requires_identity_for_active_battle_strategy(tmp_path):
    with pytest.raises(ValueError, match="canonical battle identity"):
        ControlDirectiveStore(tmp_path / "automation_ctl.json").set_strategy(
            "farm_t18",
            apply_mode="active_battle",
        )


def test_control_store_will_not_overwrite_malformed_authority(tmp_path):
    path = tmp_path / "automation_ctl.json"
    path.write_text("{not json", encoding="utf-8")
    store = ControlDirectiveStore(path)

    with pytest.raises(ControlDirectiveError):
        store.set_state("PAUSED")

    assert path.read_text(encoding="utf-8") == "{not json"


def test_status_separates_fresh_observation_from_control_and_lock_evidence(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    earlier_timestamp = (now - timedelta(seconds=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    control_path = tmp_path / "logs" / "automation_ctl.json"
    control_path.parent.mkdir(parents=True)
    control_path.write_text(
        json.dumps(
            {
                "state": "PAUSED",
                "mode": "WAIT",
                "game_speed_target": 4.5,
                "adb_port": 5555,
                "strategy": "farm_t18",
                "updated_at": now.isoformat(),
                "state_updated_at": now.isoformat(),
                "game_speed_target_updated_at": now.isoformat(),
                "adb_port_updated_at": now.isoformat(),
                "strategy_updated_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "logs" / "actions.log").write_text(
        f"[INFO {earlier_timestamp}] [CTRL] Mode set to WAIT via control file\n"
        f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file\n"
        f"[INFO {timestamp}] [CTRL] Game speed target set to x4.5 via control file\n"
        f"[INFO {timestamp}] [CTRL] ADB target set to localhost:5555 via control file\n"
        f"[INFO {timestamp}] [CTRL] Strategy set to farm_t18 via control file\n"
        f"[STATUS {timestamp}] State=RUNNING/PAUSED | Wave=520 | "
        "Coins/min=1.2T | Speed=x4.5 | Menu=UW_MENU | Secondary=[PERKS] | "
        "Overlays=[MENU_OPEN]\n",
        encoding="utf-8",
    )
    lock_path = tmp_path / "logs" / "automation-localhost_5555.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "target": "localhost:5555",
                "started_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    with lock_path.open("r", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = _service(tmp_path).status(now=now.timestamp())

    assert status["server_revision"] == CONTROL_SURFACE_REVISION
    assert status["capabilities"] == list(CONTROL_SURFACE_CAPABILITIES)
    assert status["healthy"]
    assert status["control"]["state"] == "PAUSED"
    assert status["observation"] == {
        "state": "RUNNING",
        "paused": True,
        "state_label": "RUNNING/PAUSED",
        "wave": 520,
        "coins_per_minute": "1.2T",
        "game_speed": 4.5,
        "menu": "UW_MENU",
        "secondary": ["PERKS"],
        "overlays": ["MENU_OPEN"],
        "observed_at": now.isoformat(timespec="seconds"),
        "age_seconds": 0,
        "stale": False,
    }
    assert status["runtime"]["instances"][0]["active"]
    assert status["acknowledgements"] == {
        "state": None,
        "mode": None,
        "game_speed_target": None,
        "adb_target": None,
        "strategy": None,
    }


def test_runtime_acknowledgements_survive_more_than_tail_window_of_log_output(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    owner = {
        "runtime_id": "runtime-long-log",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 7,
    }
    action_log = tmp_path / "logs" / "actions.log"
    audit_prefix = "".join(
        f"[INFO {now:%Y-%m-%d %H:%M:%S}] [CTRL] {field} acknowledged\n"
        for field in acknowledgements
        if field != "schema_version"
    )
    noisy_line = (
        f"[DEBUG {now:%Y-%m-%d %H:%M:%S}] " + ("x" * 1024) + "\n"
    )
    action_log.write_text(
        audit_prefix
        + (noisy_line * 300)
        + f"[STATUS {now:%Y-%m-%d %H:%M:%S}] "
        "State=RUNNING | Wave=500 | Coins/min=1.0T\n",
        encoding="utf-8",
    )
    assert action_log.stat().st_size > 262_144
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-long-log",
        target_generation=7,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
    )
    try:
        status = _service(tmp_path).status(now=now.timestamp())
    finally:
        lock_handle.close()

    for field in (
        "state",
        "mode",
        "game_speed_target",
        "adb_target",
        "strategy",
    ):
        assert status["acknowledgements"][field]["acknowledges_current"]
        assert status["acknowledgements"][field]["request_id"] == (
            acknowledgements[field]["request_id"]
        )
    assert status["control_model"]["action_authority"]["effective"] == (
        "enabled"
    )
    assert status["control_model"]["when_battle_ends"]["acknowledged"]
    assert status["control_model"]["strategy_scope"] == {
        "startup_default": "farm_t18",
        "active_battle": "farm_t18",
        "pending_next_boundary": None,
        "pending_active_battle": None,
        "request_id": control["strategy_request_id"],
        "observation_only": False,
        "degradation": None,
    }


def test_status_exposes_only_fresh_exact_runtime_live_metrics(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    captured_at = (now - timedelta(seconds=75)).isoformat(timespec="seconds")
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    owner = {
        "runtime_id": "runtime-live-metrics",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 13,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-live-metrics",
        target_generation=13,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
        observation_identity=ACTIVE_BATTLE_IDENTITY,
        active_run_metrics={
            "schema_version": 1,
            "status": "partial",
            "reason": "  one_or_more_metric_claims_unavailable  ",
            "active_round_identity_fingerprint": ACTIVE_BATTLE_IDENTITY,
            "captured_at": captured_at,
            "save_revision": 321,
            "checkpoint_wave": 4321,
            "whole_run": {
                "coins_per_hour": "1780000000000000000",
                "cells_per_hour": "590000",
                "cash_per_hour": "invalid",
                "waves_per_hour": "1250.5",
                "effective_game_speed": "4.984",
                "real_time_seconds": "3600",
            },
            "interval": {
                "coins_per_hour": "1810000000000000000",
                "cells_per_hour": "610000",
            },
        },
    )
    service = _service(tmp_path)
    try:
        fresh = service.status(now=now.timestamp())
        stale = service.status(now=(now + timedelta(seconds=31)).timestamp())
    finally:
        lock_handle.close()

    assert fresh["control_model"]["active_run_metrics"] == {
        "schema_version": 1,
        "status": "partial",
        "reason": "one_or_more_metric_claims_unavailable",
        "active_round_identity_fingerprint": ACTIVE_BATTLE_IDENTITY,
        "captured_at": captured_at,
        "age_seconds": 75,
        "save_revision": 321,
        "checkpoint_wave": 4321,
        "whole_run": {
            "coins_per_hour": "1780000000000000000",
            "cells_per_hour": "590000",
            "waves_per_hour": "1250.5",
            "effective_game_speed": "4.984",
        },
        "interval": {
            "coins_per_hour": "1810000000000000000",
        },
    }
    assert stale["control_model"]["active_run_metrics"] is None


def test_status_clears_rates_from_a_conflicted_live_checkpoint(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    owner = {
        "runtime_id": "runtime-conflicted-metrics",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 14,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-conflicted-metrics",
        target_generation=14,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
        observation_identity=ACTIVE_BATTLE_IDENTITY,
        active_run_metrics={
            "schema_version": 1,
            "status": "conflict",
            "reason": "real_time_seconds_regressed",
            "active_round_identity_fingerprint": ACTIVE_BATTLE_IDENTITY,
            "captured_at": now.isoformat(timespec="seconds"),
            "save_revision": 322,
            "checkpoint_wave": 4325,
            "whole_run": {"coins_per_hour": "1780000000000000000"},
            "interval": {"coins_per_hour": "1810000000000000000"},
        },
    )
    try:
        metrics = _service(tmp_path).status(now=now.timestamp())[
            "control_model"
        ]["active_run_metrics"]
    finally:
        lock_handle.close()

    assert metrics["status"] == "conflict"
    assert metrics["reason"] == "real_time_seconds_regressed"
    assert metrics["whole_run"] is None
    assert metrics["interval"] is None


@pytest.mark.parametrize(
    "metric_identity",
    [None, "b" * 64],
)
def test_status_hides_live_metrics_not_bound_to_observed_round(
    tmp_path,
    metric_identity: str | None,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    owner = {
        "runtime_id": "runtime-mismatched-live-metrics",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 15,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-mismatched-live-metrics",
        target_generation=15,
    )
    active_run_metrics = {
        "schema_version": 1,
        "status": "observed",
        "captured_at": now.isoformat(timespec="seconds"),
        "whole_run": {"coins_per_hour": "1780000000000000000"},
    }
    if metric_identity is not None:
        active_run_metrics["active_round_identity_fingerprint"] = metric_identity
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=_directive_acknowledgements(
            control,
            acknowledged_at=now.isoformat(timespec="seconds"),
        ),
        observation_identity=ACTIVE_BATTLE_IDENTITY,
        active_run_metrics=active_run_metrics,
    )
    try:
        metrics = _service(tmp_path).status(now=now.timestamp())[
            "control_model"
        ]["active_run_metrics"]
    finally:
        lock_handle.close()

    assert metrics is None


def test_runtime_acknowledgements_survive_action_log_rotation(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    owner = {
        "runtime_id": "runtime-rotated-log",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 4,
    }
    action_log = tmp_path / "logs" / "actions.log"
    action_log.write_text(
        f"[INFO {now:%Y-%m-%d %H:%M:%S}] old acknowledgement audit\n",
        encoding="utf-8",
    )
    action_log.replace(action_log.with_suffix(".log.1"))
    action_log.write_text(
        f"[STATUS {now:%Y-%m-%d %H:%M:%S}] "
        "State=RUNNING | Wave=501 | Coins/min=1.1T\n",
        encoding="utf-8",
    )
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-rotated-log",
        target_generation=4,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
    )
    try:
        status = _service(tmp_path).status(now=now.timestamp())
    finally:
        lock_handle.close()

    assert all(
        receipt is not None and receipt["acknowledges_current"]
        for receipt in status["acknowledgements"].values()
    )
    assert status["control_model"]["actions"]["capture_current_setup"][
        "code"
    ] == "available"


@pytest.mark.parametrize(
    "validation_hold",
    (
        AuthorityHold.EXCLUSIVE_VALIDATION,
        AuthorityHold.EXCLUSIVE_OWNERSHIP,
    ),
)
def test_setup_capture_waits_for_exclusive_validation_owner(
    tmp_path,
    validation_hold,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    owner = {
        "runtime_id": "runtime-validation-capture",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 8,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id=str(owner["runtime_id"]),
        target_generation=8,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=_directive_acknowledgements(
            control,
            acknowledged_at=now.isoformat(timespec="seconds"),
        ),
        holds=(
            AuthorityHoldState(
                validation_hold,
                "validation terminal cleanup owns the runtime boundary",
            ),
        ),
    )
    service = _service(tmp_path)
    try:
        availability = service.status(now=now.timestamp())["control_model"][
            "actions"
        ]["capture_current_setup"]
        assert availability == {
            "available": False,
            "code": "exclusive_validation_active",
            "reason": (
                "complete exclusive validation before capturing current setup"
            ),
        }

        with pytest.raises(ControlSurfaceRequestError) as busy:
            service.apply_setup_capture({"operation": "request"})

        assert busy.value.status == 409
        assert busy.value.code == "exclusive_validation_active"
        assert service.control_store.status().get("setup_capture") is None
    finally:
        lock_handle.close()


@pytest.mark.parametrize("legacy_strategy_receipt", (None, "farm_t19"))
def test_authoritative_strategy_scope_wins_over_legacy_acknowledgements(
    tmp_path,
    legacy_strategy_receipt,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    if legacy_strategy_receipt is None:
        acknowledgements["strategy"] = None
    else:
        acknowledgements["strategy"] = {
            "value": legacy_strategy_receipt,
            "request_id": control["strategy_request_id"],
            "acknowledged_at": now.isoformat(timespec="seconds"),
        }
    owner = {
        "runtime_id": "runtime-strategy-scope",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 6,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-strategy-scope",
        target_generation=6,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
        strategy_scope={
            "startup_default": "farm_t19_ad_assist",
            "active_battle": "farm_t19_ad_assist",
            "pending_next_boundary": None,
            "pending_active_battle": None,
        },
    )
    try:
        status = _service(tmp_path).status(now=now.timestamp())
    finally:
        lock_handle.close()

    assert status["acknowledgements"]["strategy"] is None or not status[
        "acknowledgements"
    ]["strategy"]["acknowledges_current"]
    assert status["control_model"]["strategy_scope"] == {
        "startup_default": "farm_t19_ad_assist",
        "active_battle": "farm_t19_ad_assist",
        "pending_next_boundary": None,
        "pending_active_battle": None,
        "request_id": control["strategy_request_id"],
        "observation_only": False,
        "degradation": None,
    }


@pytest.mark.parametrize(
    (
        "runtime_id",
        "pid",
        "adb_target",
        "target_generation",
        "age_seconds",
    ),
    (
        ("prior-runtime", os.getpid(), "localhost:5555", 9, 0),
        ("current-runtime", os.getpid() + 1000, "localhost:5555", 9, 0),
        ("current-runtime", os.getpid(), "localhost:5565", 9, 0),
        ("current-runtime", os.getpid(), "localhost:5555", 8, 0),
        ("current-runtime", os.getpid(), "localhost:5555", 9, 31),
    ),
)
def test_runtime_acknowledgements_reject_stale_or_wrong_runtime_owner(
    tmp_path,
    runtime_id,
    pid,
    adb_target,
    target_generation,
    age_seconds,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    control = _control_with_all_request_identities(tmp_path)
    acknowledgements = _directive_acknowledgements(
        control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="current-runtime",
        target_generation=9,
    )
    published_at = now - timedelta(seconds=age_seconds)
    _publish_runtime_acknowledgements(
        tmp_path,
        now=published_at,
        owner={
            "runtime_id": runtime_id,
            "pid": pid,
            "adb_target": adb_target,
            "target_generation": target_generation,
        },
        acknowledgements=acknowledgements,
    )
    try:
        status = _service(tmp_path).status(now=now.timestamp())
    finally:
        lock_handle.close()

    assert all(
        receipt is None for receipt in status["acknowledgements"].values()
    )
    assert status["control_model"]["action_authority"]["effective"] in {
        "pending",
        "unknown",
    }


def test_same_value_request_stays_pending_until_exact_request_id_replaces_ack(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    store = ControlDirectiveStore(tmp_path / "logs" / "automation_ctl.json")
    first = store.set_state("RUNNING", source="first")
    store.set_mode("WAIT", source="test")
    store.set_game_speed_target(4.5, source="test")
    store.set_adb_port(5555, source="test")
    store.set_strategy("farm_t18", source="test")
    first_control = store.status()
    acknowledgements = _directive_acknowledgements(
        first_control,
        acknowledged_at=now.isoformat(timespec="seconds"),
    )
    owner = {
        "runtime_id": "runtime-request-replacement",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 12,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id="runtime-request-replacement",
        target_generation=12,
    )
    _publish_runtime_acknowledgements(
        tmp_path,
        now=now,
        owner=owner,
        acknowledgements=acknowledgements,
    )
    second = store.set_state("RUNNING", source="replacement")
    try:
        pending = _service(tmp_path).status(now=now.timestamp())
        assert pending["acknowledgements"]["state"]["request_id"] == (
            first["state_request_id"]
        )
        assert not pending["acknowledgements"]["state"][
            "acknowledges_current"
        ]
        assert pending["control_model"]["action_authority"]["effective"] == (
            "pending"
        )

        acknowledgements["state"] = {
            "value": "RUNNING",
            "request_id": second["state_request_id"],
            "acknowledged_at": now.isoformat(timespec="seconds"),
        }
        _publish_runtime_acknowledgements(
            tmp_path,
            now=now,
            owner=owner,
            acknowledgements=acknowledgements,
        )
        current = _service(tmp_path).status(now=now.timestamp())
    finally:
        lock_handle.close()

    assert current["acknowledgements"]["state"]["request_id"] == (
        second["state_request_id"]
    )
    assert current["acknowledgements"]["state"]["acknowledges_current"]
    assert current["control_model"]["action_authority"]["effective"] == (
        "enabled"
    )


def test_status_projection_uses_battle_identity_bound_current_save_perks(tmp_path):
    _write_current_run_scope(tmp_path, run_id="battle-perks-status")
    presentation = _current_perks_presentation()
    timeline_path = (
        tmp_path / "logs" / "automation_ctl.perk_timeline_state.json"
    )
    timeline_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activity_scope_run_id": "battle-perks-status",
                "route_open": False,
                "tracker": {},
                "current_perks": presentation,
            }
        ),
        encoding="utf-8",
    )

    service = _service(tmp_path)
    current_battle_perks = service._current_battle_perks(
        {"run_id": "unrelated-report-scope"},
        battle_identity="battle-perks-status",
    )
    status = service.status()

    assert current_battle_perks == presentation
    assert status["current_battle_perks"]["reason"] == (
        "battle_identity_unavailable"
    )
    assert "current_battle_perks_v1" in status["capabilities"]


def test_terminal_attestation_requires_exact_pause_and_stores_audit_pair(
    tmp_path,
):
    service = _service(tmp_path)
    identity_values = {
        "game_version": 1102,
        "current_tier": 19,
        "rounds_started_this_tier": 319,
        "round_seed": 1721080409,
    }
    identity_fingerprint = hashlib.sha256(
        json.dumps(
            identity_values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    identity_path = tmp_path / "logs" / "battle_identity.json"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "active",
                "identity": {
                    **identity_values,
                    "fingerprint": identity_fingerprint,
                },
                "bound_at": "2026-08-16T06:33:00+00:00",
                "reason": "battle_started",
                "operation_id": "launch-1",
                "acquisition": {},
            }
        ),
        encoding="utf-8",
    )
    assert service.battle_identity_store.record_session_preflight(
        identity_fingerprint=identity_fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    status = _legacy_terminal_attestation_status(identity_fingerprint)
    service.status = Mock(return_value=status)
    strategy = SimpleNamespace(
        name="farm_t19",
        definition_fingerprint=lambda: "d" * 64,
        session_preflight_fingerprint=lambda: "e" * 64,
        run_configuration=lambda: {"profile": "farm", "tier": 19},
    )

    with patch("core.control_surface.get_strategy", return_value=strategy):
        result = service.apply_terminal_evidence_attestation(
            {
                "confirmation": (
                    "terminal_and_strategy_unchanged_since_battle"
                ),
                "expected_active_round_identity_fingerprint": (
                    identity_fingerprint
                ),
                "reason": "Operator confirmed no terminal or Strategy change",
            }
        )

    assert result["status"] == "attested"
    retained = service.battle_identity_store.active()
    assert retained is not None
    assert retained.strategy_snapshot is not None
    assert retained.operator_terminal_attestation is not None
    assert retained.strategy_snapshot["provenance"]["kind"] == (
        "operator_terminal_attestation"
    )
    activity = (tmp_path / "logs" / "actions.log").read_text(
        encoding="utf-8"
    )
    assert activity.count("[ACTION ") == 1
    assert activity.count("[RESULT ") == 1
    assert activity.count(result["operation_id"]) == 2


def test_terminal_attestation_fails_closed_when_pause_is_not_exact(tmp_path):
    service = _service(tmp_path)
    status = _legacy_terminal_attestation_status("a" * 64)
    status["control"]["state"] = "RUNNING"
    service.status = Mock(return_value=status)

    with pytest.raises(ControlSurfaceRequestError) as rejected:
        service.apply_terminal_evidence_attestation(
            {
                "confirmation": (
                    "terminal_and_strategy_unchanged_since_battle"
                ),
                "expected_active_round_identity_fingerprint": "a" * 64,
                "reason": "Operator confirmed no terminal or Strategy change",
            }
        )

    assert rejected.value.code == "pause_not_exactly_acknowledged"
    assert not (tmp_path / "logs" / "battle_identity.json").exists()
    assert not (tmp_path / "logs" / "actions.log").exists()


def test_status_exposes_local_mapping_lifecycle_without_blocking_health(tmp_path):
    mapping_status = {
        "schema_version": 1,
        "available": True,
        "blocks_startup": False,
        "items": [
            {
                "mapping_id": "data-9-game-1101",
                "check_id": "modules",
                "value_kind": "module_info_index",
                "raw_value": 41,
                "semantic_value": "Being Annihilator",
                "scope": {
                    "slot_key": "cannon_assist",
                    "family": "cannon",
                    "role": "assist",
                },
                "state": "active_local",
                "reason": "canonical integration is pending",
            }
        ],
        "counts": {"active_local": 1},
        "reason": "",
    }
    lock_handle = _fresh_runtime_lock(tmp_path, state="HOME_SCREEN")
    try:
        service = _service(tmp_path)
        candidate_status = {
            "schema_version": 2,
            "capability": "save_mapping_review_status_v2",
            "available": True,
            "items": [],
            "counts": {},
            "reason": "",
        }
        service.save_mapping_integration_manager.status = Mock(
            return_value=candidate_status
        )
        with patch(
            "core.control_surface.confirmed_local_mapping_status",
            return_value=mapping_status,
        ) as status_projection:
            status = service.status()
    finally:
        lock_handle.close()

        status_projection.assert_called_once_with(
            store=service.confirmed_local_mapping_store,
            candidate_store=service.mapping_candidate_store,
            repository_root=service.repository_root,
            candidate_status=candidate_status,
        )
    assert status["confirmed_local_mappings"] == mapping_status
    assert status["healthy"]
    assert not status["confirmed_local_mappings"]["blocks_startup"]


def test_save_mapping_integration_catalog_and_review_are_non_mutating(tmp_path):
    service = _service(tmp_path)
    catalog = {
        "schema_version": 3,
        "capability": "save_mapping_staged_candidate_v1",
        "available": True,
        "reason": "",
        "repository": {},
        "items": [],
    }
    review = {
        "operation": "review",
        "candidate_record_id": "a" * 64,
        "reviewed_proposal_fingerprint": "b" * 64,
        "proposal": {"schema_version": 2, "targets": []},
        "stage": {
            "available": False,
            "code": "staging_ref_occupied",
            "reason": "pending promotion",
        },
    }
    service.save_mapping_integration_manager.catalog = Mock(return_value=catalog)
    service.save_mapping_integration_manager.review = Mock(return_value=review)

    assert service.save_mapping_integration() == catalog
    assert service.apply_save_mapping_integration(
        {
            "operation": "review",
            "candidate_record_id": "a" * 64,
        }
    ) == review
    service.save_mapping_integration_manager.review.assert_called_once_with(
        candidate_record_id="a" * 64,
    )
    assert not (tmp_path / "logs" / "actions.log").exists()


def test_automatic_save_mapping_reconciliation_is_audited_as_one_pair(tmp_path):
    service = _service(tmp_path)
    plan = {
        "capability": "save_mapping_automatic_promotion_v1",
        "needed": True,
        "action": "machine_verify_and_integrate",
        "candidate_record_id": "a" * 64,
    }
    promoted = {
        "operation": "integrate",
        "disposition": "promoted",
        "candidate_record_id": "a" * 64,
        "staged_commit": "b" * 40,
        "promoted": True,
        "published": True,
    }
    manager = service.save_mapping_integration_manager
    manager.automatic_reconciliation_plan = Mock(return_value=plan)
    manager.reconcile_automatic = Mock(return_value=promoted)

    result = service.reconcile_save_mapping_integration()

    assert result == promoted
    activity = (tmp_path / "logs" / "actions.log").read_text(
        encoding="utf-8"
    )
    assert activity.count("[ACTION ") == 1
    assert activity.count("[RESULT ") == 1
    assert "action=machine_verify_and_integrate" in activity
    assert "disposition=promoted" in activity
    operation_ids = [
        line.partition("[OPERATION] id=")[2]
        for line in activity.splitlines()
        if "[OPERATION] id=" in line
    ]
    assert len(set(operation_ids)) == 1


def test_idle_save_mapping_reconciliation_does_not_write_audit(tmp_path):
    service = _service(tmp_path)
    idle = {
        "capability": "save_mapping_automatic_promotion_v1",
        "needed": False,
        "action": "idle",
        "candidate_record_id": None,
        "reason": "",
    }
    service.save_mapping_integration_manager.automatic_reconciliation_plan = (
        Mock(return_value=idle)
    )

    assert service.reconcile_save_mapping_integration() == idle
    assert not (tmp_path / "logs" / "actions.log").exists()


def test_save_mapping_reconciliation_loop_runs_immediately_and_backs_off():
    service = Mock()
    service.reconcile_save_mapping_integration.side_effect = [
        {"disposition": "promotion_queued"},
        {"disposition": "promotion_queued"},
        {"disposition": "promoted"},
    ]
    delays: list[int] = []

    class StopAfterThreeAttempts:
        def wait(self, delay: int) -> bool:
            delays.append(delay)
            return len(delays) == 4

    _save_mapping_reconciliation_loop(service, StopAfterThreeAttempts())

    assert service.reconcile_save_mapping_integration.call_count == 3
    assert delays == [0, 5, 10, 5]


def test_save_mapping_integrate_requires_exact_review_and_logs_one_pair(tmp_path):
    service = _service(tmp_path)
    review = {
        "operation": "review",
        "candidate_record_id": "a" * 64,
        "reviewed_proposal_fingerprint": "b" * 64,
        "proposal": {
            "schema_version": 2,
            "targets": [{"path": "one.json"}, {"path": "two.json"}],
        },
        "stage": {"available": True, "code": "", "reason": ""},
    }
    integrated = {
        "operation": "integrate",
        "disposition": "promoted",
        "staged_commit": "e" * 40,
        "committed": True,
        "promoted": True,
        "published": True,
        "mapping_invariants": "passed",
    }
    service.save_mapping_integration_manager.review = Mock(return_value=review)
    service.save_mapping_integration_manager.integrate_reviewed = Mock(
        return_value=integrated
    )

    result = service.apply_save_mapping_integration(
        {
            "operation": "stage",
            "candidate_record_id": "a" * 64,
            "reviewed_proposal_fingerprint": "b" * 64,
        }
    )

    assert result == integrated
    activity = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert activity.count("[ACTION ") == 1
    assert activity.count("[RESULT ") == 1
    operation_ids = [
        line.partition("[OPERATION] id=")[2]
        for line in activity.splitlines()
        if "[OPERATION] id=" in line
    ]
    assert len(set(operation_ids)) == 1
    assert operation_ids[0].startswith(
        "save-mapping-aaaaaaaaaaaa-bbbbbbbbbbbb-"
    )
    assert len(operation_ids[0].rsplit("-", 1)[1]) == 12
    assert (
        "staged=true promoted=true published=true mapping_invariants=passed"
        in activity
    )


def test_save_mapping_dismiss_preserves_evidence_and_logs_one_pair(tmp_path):
    service = _service(tmp_path)
    dismissed = {
        "capability": "save_mapping_candidate_disposition_v1",
        "operation": "dismiss",
        "disposition": "dismissed",
        "candidate_record_id": "a" * 64,
        "event_id": "b" * 64,
        "changed": True,
        "evidence_preserved": True,
    }
    service.save_mapping_integration_manager.dismiss = Mock(
        return_value=dismissed
    )

    result = service.apply_save_mapping_integration(
        {
            "operation": "dismiss",
            "candidate_record_id": "a" * 64,
        }
    )

    assert result == dismissed
    service.save_mapping_integration_manager.dismiss.assert_called_once_with(
        candidate_record_id="a" * 64,
    )
    activity = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert activity.count("[ACTION ") == 1
    assert activity.count("[RESULT ") == 1
    assert "evidence=preserved" in activity
    assert "disposition=dismissed" in activity
    operation_ids = [
        line.partition("[OPERATION] id=")[2]
        for line in activity.splitlines()
        if "[OPERATION] id=" in line
    ]
    assert len(set(operation_ids)) == 1
    assert operation_ids[0].startswith("save-mapping-dismiss-aaaaaaaaaaaa-")


def test_save_mapping_integrate_attempts_have_unique_audit_identities(tmp_path):
    service = _service(tmp_path)
    review = {
        "reviewed_proposal_fingerprint": "b" * 64,
        "proposal": {"schema_version": 2, "targets": [{"path": "one.json"}]},
        "stage": {"available": True, "code": "", "reason": ""},
    }
    service.save_mapping_integration_manager.review = Mock(return_value=review)
    service.save_mapping_integration_manager.integrate_reviewed = Mock(
        side_effect=SaveMappingIntegrationError(
            "commit_state_uncertain",
            "Inspect main, develop, and the transaction.",
        )
    )
    request = {
        "operation": "stage",
        "candidate_record_id": "a" * 64,
        "reviewed_proposal_fingerprint": "b" * 64,
    }

    for _ in range(2):
        with pytest.raises(ControlSurfaceRequestError) as failure:
            service.apply_save_mapping_integration(request)
        assert failure.value.status == 503
        assert failure.value.code == "commit_state_uncertain"

    activity = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    action_ids = [
        line.partition("[OPERATION] id=")[2]
        for line in activity.splitlines()
        if line.startswith("[ACTION ")
    ]
    result_ids = [
        line.partition("[OPERATION] id=")[2]
        for line in activity.splitlines()
        if line.startswith("[RESULT ")
    ]
    assert len(action_ids) == 2
    assert len(set(action_ids)) == 2
    assert sorted(action_ids) == sorted(result_ids)
    assert activity.count("disposition=unconfirmed code=commit_state_uncertain") == 2


def test_post_ref_transaction_write_failure_is_audited_as_unconfirmed(tmp_path):
    service = _service(tmp_path)
    service.save_mapping_integration_manager.integrate_reviewed = Mock(
        side_effect=SaveMappingIntegrationError(
            "transaction_write_failed",
            "The durable phase update could not be confirmed.",
        )
    )

    with pytest.raises(ControlSurfaceRequestError) as failure:
        service.apply_save_mapping_integration(
            {
                "operation": "stage",
                "candidate_record_id": "a" * 64,
                "reviewed_proposal_fingerprint": "b" * 64,
            }
        )

    assert failure.value.status == 503
    activity = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert "disposition=unconfirmed code=transaction_write_failed" in activity


def test_legacy_save_mapping_prepare_operation_is_rejected_without_audit(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(
        ControlSurfaceRequestError,
        match="review, dismiss, or stage",
    ):
        service.apply_save_mapping_integration(
            {
                "operation": "prepare",
                "candidate_record_id": "a" * 64,
                "workspace_id": "c" * 64,
                "reviewed_proposal_fingerprint": "b" * 64,
            }
        )

    assert not (tmp_path / "logs" / "actions.log").exists()


def test_save_mapping_integrate_audits_stale_fingerprint_rejection(tmp_path):
    service = _service(tmp_path)
    service.save_mapping_integration_manager.integrate_reviewed = Mock(
        side_effect=SaveMappingIntegrationError(
            "reviewed_proposal_stale",
            "The reviewed proposal changed.",
        )
    )

    with pytest.raises(ControlSurfaceRequestError) as failure:
        service.apply_save_mapping_integration(
            {
                "operation": "stage",
                "candidate_record_id": "a" * 64,
                "reviewed_proposal_fingerprint": "d" * 64,
            }
        )

    assert failure.value.status == 409
    assert failure.value.code == "reviewed_proposal_stale"
    activity = (tmp_path / "logs" / "actions.log").read_text(encoding="utf-8")
    assert activity.count("[ACTION ") == 1
    assert activity.count("[RESULT ") == 1
    assert "disposition=failed code=reviewed_proposal_stale" in activity


@pytest.mark.parametrize(
    ("candidate_id", "fingerprint"),
    [
        ("a" * 63, "b" * 64),
        ("A" * 64, "b" * 64),
        ("a" * 63 + "\n", "b" * 64),
        ("a" * 64, "b" * 63 + "\n"),
    ],
)
def test_save_mapping_integrate_rejects_malformed_identity_without_audit(
    tmp_path,
    candidate_id,
    fingerprint,
):
    service = _service(tmp_path)

    with pytest.raises(ControlSurfaceRequestError):
        service.apply_save_mapping_integration(
            {
                "operation": "stage",
                "candidate_record_id": candidate_id,
                "reviewed_proposal_fingerprint": fingerprint,
            }
        )

    assert not (tmp_path / "logs" / "actions.log").exists()


def test_save_mapping_integration_requests_are_exact_shape(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(ControlSurfaceRequestError, match="accepts exactly"):
        service.apply_save_mapping_integration(
            {
                "operation": "review",
                "candidate_record_id": "a" * 64,
                "workspace_id": "b" * 64,
                "path": "/client/supplied/path",
            }
        )

    with pytest.raises(ControlSurfaceRequestError, match="accepts exactly"):
        service.apply_save_mapping_integration(
            {
                "operation": "dismiss",
                "candidate_record_id": "a" * 64,
                "reason": "client supplied",
            }
        )


def test_status_never_exposes_perks_from_another_battle_identity(tmp_path):
    _write_current_run_scope(tmp_path, run_id="new-battle")
    timeline_path = (
        tmp_path / "logs" / "automation_ctl.perk_timeline_state.json"
    )
    timeline_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activity_scope_run_id": "old-battle",
                "route_open": False,
                "tracker": {},
                "current_perks": _current_perks_presentation(),
            }
        ),
        encoding="utf-8",
    )

    current = _service(tmp_path)._current_battle_perks(
        {"run_id": "unrelated-report-scope"},
        battle_identity="new-battle",
    )

    assert current["status"] == "awaiting_save_checkpoint"
    assert current["reason"] == "current_run_checkpoint_unavailable"
    assert current["items"] == []


def test_status_projection_rejects_internally_inconsistent_current_perks(tmp_path):
    _write_current_run_scope(tmp_path, run_id="invalid-perks")
    presentation = _current_perks_presentation()
    presentation["picked_count"] = 4
    timeline_path = (
        tmp_path / "logs" / "automation_ctl.perk_timeline_state.json"
    )
    timeline_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "activity_scope_run_id": "invalid-perks",
                "route_open": False,
                "tracker": {},
                "current_perks": presentation,
            }
        ),
        encoding="utf-8",
    )

    current = _service(tmp_path)._current_battle_perks(
        None,
        battle_identity="invalid-perks",
    )

    assert current["status"] == "unavailable"
    assert current["reason"] == "current_perks_projection_invalid"
    assert current["items"] == []


def test_status_serializes_fresh_runtime_owned_strategy_gate(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-status",
        battle_identity="a" * 64,
        primary_state="RUNNING",
    )
    gate = authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope="run-status",
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules", "target_priority"),
        reason="Modules and Target Priority do not match",
        now=now.timestamp() - 3,
    )
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner={
            "runtime_id": "runtime-status",
            "pid": os.getpid(),
            "adb_target": "localhost:5555",
        },
        stale_after_seconds=30,
    )
    assert publisher.publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
    )
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
    try:
        status = service.status(now=now.timestamp())
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    published = status["strategy_action_gate"]
    assert published["available"] is True
    assert published["active"] is True
    assert published["stale"] is False
    assert published["owner_matches_active_runtime"] is True
    assert published["gate_id"] == gate.gate_id
    assert published["strategy"] == "farm_t18"
    assert published["battle_scope"] == "run-status"
    assert published["source"] == "session_preflight"
    assert published["phase"] == "running_battle"
    assert published["failed_check_ids"] == ["modules", "target_priority"]
    assert published["reason"] == "Modules and Target Priority do not match"
    assert published["observation_authority"]["allowed"] is True
    assert published["auxiliary_collection_authority"]["allowed"] is True
    assert published["strategy_action_authority"]["allowed"] is False
    assert published["lifecycle_action_authority"]["allowed"] is False
    assert "in_battle_ad_gem" in published["allowed_auxiliary_collectors"]
    assert "daily_gem_store" in published["allowed_auxiliary_collectors"]
    assert status["control"]["state"] == "RUNNING"


def test_status_exposes_idle_home_collector_without_battle_authority(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="HOME_SCREEN")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=False,
        battle_scope="run-home",
        primary_state="HOME_SCREEN",
        holds=(
            AuthorityHoldState(
                AuthorityHold.OPERATOR_WORKFLOW,
                (
                    "runtime is waiting for explicit Start Battle or Attach "
                    "to Battle intent"
                ),
                allowed_auxiliary_collectors=(
                    AuxiliaryCollector.HOME_AD_GEM,
                ),
            ),
        ),
    )
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner={
            "runtime_id": "runtime-home",
            "pid": os.getpid(),
            "adb_target": "localhost:5555",
        },
        stale_after_seconds=30,
    )
    assert publisher.publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
    )
    try:
        published = _service(tmp_path).status(now=now.timestamp())[
            "strategy_action_gate"
        ]
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert published["available"] is True
    assert published["auxiliary_collection_authority"]["allowed"] is True
    assert published["allowed_auxiliary_collectors"] == ["home_ad_gem"]
    assert published["strategy_action_authority"]["allowed"] is False
    assert published["lifecycle_action_authority"]["allowed"] is False
    assert published["holds"] == [
        {
            "hold": "operator_workflow",
            "reason": (
                "runtime is waiting for explicit Start Battle or Attach to "
                "Battle intent"
            ),
            "allowed_auxiliary_collectors": ["home_ad_gem"],
        }
    ]


def test_strategy_gate_status_rejects_stale_inactive_or_wrong_owner_snapshot(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-status",
        primary_state="RUNNING",
    )
    authority.activate_strategy_gate(
        strategy="farm_t18",
        battle_scope="run-status",
        source="session_preflight",
        phase="running_battle",
        failed_check_ids=("modules",),
        reason="Modules do not match",
    )
    path = tmp_path / "logs" / "strategy_action_gate.json"
    matching = RuntimeActionAuthorityPublisher(
        path,
        owner={
            "runtime_id": "runtime-status",
            "pid": os.getpid(),
            "adb_target": "localhost:5555",
        },
        stale_after_seconds=10,
    )
    service = _service(tmp_path)
    try:
        matching.publish(
            authority.snapshot(now=now.timestamp() - 11),
            now=now.timestamp() - 11,
        )
        assert service.status(now=now.timestamp())["strategy_action_gate"][
            "stale"
        ]

        matching.publish(
            authority.snapshot(now=now.timestamp()),
            runtime_active=False,
            now=now.timestamp(),
        )
        inactive = service.status(now=now.timestamp())["strategy_action_gate"]
        assert inactive["stale"]
        assert inactive["runtime_active"] is False

        wrong_owner = RuntimeActionAuthorityPublisher(
            path,
            owner={
                "runtime_id": "other-runtime",
                "pid": os.getpid() + 1000,
                "adb_target": "localhost:5555",
            },
        )
        wrong_owner.publish(
            authority.snapshot(now=now.timestamp()),
            now=now.timestamp(),
        )
        mismatched = service.status(now=now.timestamp())[
            "strategy_action_gate"
        ]
        assert mismatched["stale"]
        assert mismatched["owner_matches_active_runtime"] is False
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_strategy_gate_status_never_scrapes_warning_text(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[WARN 2026-08-02 12:00:00] [STRATEGY_GATE] Entered running-battle "
        "gate fake: Modules do not match\n",
        encoding="utf-8",
    )

    gate = _service(tmp_path).status()["strategy_action_gate"]

    assert gate["available"] is False
    assert gate["active"] is False
    assert gate["stale"] is True


def _host_maintenance_context(
    root: Path,
    *,
    strategy: str = "farm_t18",
    control_state: str = "RUNNING",
    runtime_adb_port: int = 5555,
    windows_adb_port: int = 5555,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    run_id = "run-emulator-recovery"
    owner = {
        "runtime_id": "runtime-emulator-recovery",
        "pid": os.getpid(),
        "adb_target": f"localhost:{runtime_adb_port}",
        "target_generation": 11,
    }
    lock_handle = _fresh_exact_runtime_lock(
        root,
        runtime_id=owner["runtime_id"],
        target_generation=owner["target_generation"],
        target=owner["adb_target"],
    )
    _write_current_run_scope(root, run_id=run_id)
    service = _service(root)
    service.control_store.set_strategy(
        "farm_t19" if strategy == "gc_farm_t19_experiment" else strategy,
        source="test",
    )
    control = service.control_store.set_state(control_state, source="test")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=control_state != "RUNNING",
        active_battle=True,
        battle_scope=run_id,
        battle_identity=ACTIVE_BATTLE_IDENTITY,
        primary_state="RUNNING",
    )
    publisher = RuntimeActionAuthorityPublisher(
        root / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    )
    publisher.publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
        control_model={
            "schema_version": 1,
            "observation": None,
            "strategy_scope": {"active_battle": strategy},
        },
    )
    listener = {
        "host_id": "WINDOWS-HOST",
        "adb_port": windows_adb_port,
        "process_id": 90,
        "process_started_at": "2026-08-10T10:00:00.1234567+00:00",
        "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
        "instance_name": "Nougat32",
    }
    return (
        now,
        run_id,
        owner,
        lock_handle,
        service,
        control,
        authority,
        publisher,
        listener,
    )


def test_degradation_status_excludes_mixed_host_active_run_intervals(tmp_path):
    service = _service(tmp_path)
    host_id = "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686"
    assessment = {
        "schema_version": 1,
        "assessed_at": "2026-08-15T20:00:00+00:00",
        "status": "insufficient_history",
        "automatic_ready": False,
        "reason": "not enough history",
        "candidate_battle_ids": [],
        "baseline_battle_ids": [],
    }
    runtime_authority = {
        "runtime_battle_identity": "battle-a",
        "control_model": {
            "strategy_scope": {"active_battle": "farm_t18"},
            "active_run_performance": {
                "checkpoints": [
                    {"captured_at": "2026-08-15T19:55:00+00:00"}
                ]
            },
            "emulator_location_round": {
                "selection_count": 2,
                "coverage_complete": True,
                "mixed_hosts": True,
            },
        },
    }
    control = {
        "state": "RUNNING",
        "emulator_location": {
            "host_id": host_id,
        },
    }

    with (
        patch.object(
            service.host_performance_store,
            "current_bluestacks_lifetime_marker",
            return_value=None,
        ) as marker,
        patch.object(
            service.host_performance_store,
            "recent_bluestacks_lifetime_aggregates",
            return_value=[],
        ),
        patch.object(
            service.host_performance_store,
            "bluestacks_lifetime_handle_summary",
            return_value=None,
        ),
        patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=assessment,
        ) as assess,
    ):
        service._emulator_degradation_status(
            control=control,
            runtime_authority=runtime_authority,
            current_run=None,
            host_maintenance={},
            now=datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc).timestamp(),
        )

    marker.assert_called_once_with(
        current_run_id="battle-a",
        host_id=host_id,
    )
    assert assess.call_args.kwargs["active_run_performance"] is None


def test_host_maintenance_handshake_is_runtime_bound_and_idempotent(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    run_id = "run-emulator-recovery"
    owner = {
        "runtime_id": "runtime-emulator-recovery",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
        "target_generation": 11,
    }
    lock_handle = _fresh_exact_runtime_lock(
        tmp_path,
        runtime_id=owner["runtime_id"],
        target_generation=owner["target_generation"],
    )
    _write_current_run_scope(tmp_path, run_id=run_id)
    service = _service(tmp_path)
    service.control_store.set_strategy("farm_t18", source="test")
    control = service.control_store.set_state("RUNNING", source="test")
    bound_runtime = {
        **owner,
        "state_request_id": control["state_request_id"],
    }
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope=run_id,
        battle_identity=ACTIVE_BATTLE_IDENTITY,
        primary_state="RUNNING",
    )
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    )
    publisher.publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
        control_model={
            "schema_version": 1,
            "observation": None,
            "strategy_scope": {"active_battle": "farm_t18"},
        },
    )
    listener = {
        "host_id": "WINDOWS-HOST",
        "adb_port": 5555,
        "process_id": 90,
        "process_started_at": "2026-08-10T10:00:00+00:00",
        "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
        "instance_name": "Nougat32",
    }
    detector_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "current_run_id": run_id,
        "current_strategy": "farm_t18",
        "status": "automatic_ready",
        "automatic_ready": True,
        "reason": "two slow runs and sustained handle growth",
        "candidate_battle_ids": ["BattleSlow1", "BattleSlow2"],
        "baseline_battle_ids": ["BattleBase1", "BattleBase2", "BattleBase3"],
        "candidate_cph_ratio": 0.88,
        "individual_cph_ratios": [0.87, 0.89],
        "effective_game_speed_ratio": 0.99,
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
            "sample_count": 120,
            "handle_ratio": 1.9,
            "handle_delta": 4_500,
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=detector_ready,
        ) as detector:
            requested = service.apply_host_maintenance(
                {"operation": "request", **listener},
                now=now.timestamp(),
            )
        assert detector.call_count == 1
        assert detector.call_args.kwargs["current_strategy"] == "farm_t18"
        maintenance = requested["host_maintenance"]["request"]
        assert maintenance["state"] == "requested"
        assert maintenance["runtime"] == bound_runtime
        assert maintenance["battle_scope"] == ACTIVE_BATTLE_IDENTITY
        assert maintenance["initiator"] == "automatic_detector"
        assert {
            key: maintenance["host_target"][key] for key in listener
        } == listener
        assert maintenance["source"] == "windows-control-surface"
        assert maintenance["trigger"]["request_kind"] == (
            "automatic_detector"
        )
        request_id = maintenance["request_id"]

        authority.update_context(
            global_pause=False,
            active_battle=True,
            battle_scope=run_id,
            battle_identity=ACTIVE_BATTLE_IDENTITY,
            primary_state="RUNNING",
            holds=(
                AuthorityHoldState(
                    AuthorityHold.EMULATOR_MAINTENANCE,
                    "BlueStacks maintenance owns host and game recovery",
                ),
            ),
        )
        runtime_ack = {
            "schema_version": 1,
            "request_id": request_id,
            "state": "host_restart_authorized",
            "runtime": bound_runtime,
            "battle_scope": ACTIVE_BATTLE_IDENTITY,
            "high_water_wave": 2_000,
            "intro_sprint_active": False,
            "replay_active": False,
            "exclude_from_degradation": True,
            "reason": "runtime hold installed",
            "observed_at": now.isoformat(),
        }
        publisher.publish(
            authority.snapshot(now=now.timestamp() + 1),
            now=now.timestamp() + 1,
            control_model={
                "schema_version": 1,
                "emulator_maintenance": runtime_ack,
                "observation": None,
                "strategy_scope": {"active_battle": "farm_t18"},
            },
        )
        authorized = service.status(now=now.timestamp() + 1)[
            "host_maintenance"
        ]
        assert authorized["host_restart_authorized"] is True
        assert authorized["hold_installed"] is True

        mismatched_scope_ack = {
            **runtime_ack,
            "battle_scope": "different-run",
        }
        publisher.publish(
            authority.snapshot(now=now.timestamp() + 1.25),
            now=now.timestamp() + 1.25,
            control_model={
                "schema_version": 1,
                "emulator_maintenance": mismatched_scope_ack,
                "observation": None,
                "strategy_scope": {"active_battle": "farm_t18"},
            },
        )
        scope_mismatch = service.status(now=now.timestamp() + 1.25)[
            "host_maintenance"
        ]
        assert scope_mismatch["host_restart_authorized"] is False
        assert scope_mismatch["owner_matches_request"] is False

        publisher.publish(
            authority.snapshot(now=now.timestamp() + 1.5),
            now=now.timestamp() + 1.5,
            control_model={
                "schema_version": 1,
                "emulator_maintenance": runtime_ack,
                "observation": None,
                "strategy_scope": {"active_battle": "farm_t18"},
            },
        )

        ack_payload = {
            "operation": "acknowledge",
            "request_id": request_id,
            "host_id": "WINDOWS-HOST",
            "adb_port": 5555,
            "process_id": 90,
            "process_started_at": "2026-08-10T10:00:00+00:00",
            "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            "instance_name": "Nougat32",
        }
        acknowledged = service.apply_host_maintenance(
            ack_payload,
            now=now.timestamp() + 2,
        )
        repeated_ack = service.apply_host_maintenance(
            ack_payload,
            now=now.timestamp() + 3,
        )
        assert acknowledged["host_maintenance"]["request"]["state"] == (
            "host_acknowledged"
        )
        assert repeated_ack["host_maintenance"]["request"]["host_ack"] == (
            acknowledged["host_maintenance"]["request"]["host_ack"]
        )

        completion_payload = {
            "operation": "complete",
            "request_id": request_id,
            "host_id": "WINDOWS-HOST",
            "adb_port": 5555,
            "process_id": 91,
            "process_started_at": "2026-08-10T10:04:00+00:00",
            "previous_process_id": 90,
            "previous_process_started_at": "2026-08-10T10:00:00+00:00",
            "executable_path": r"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            "instance_name": "Nougat32",
        }
        completed = service.apply_host_maintenance(
            completion_payload,
            now=now.timestamp() + 4,
        )
        repeated_completion = service.apply_host_maintenance(
            completion_payload,
            now=now.timestamp() + 5,
        )
        assert completed["host_maintenance"]["request"]["state"] == (
            "host_restarted"
        )
        assert repeated_completion["host_maintenance"]["request"] == (
            completed["host_maintenance"]["request"]
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_host_maintenance_request_rejects_an_unready_detector(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ControlSurfaceRequestError) as rejected:
        service.apply_host_maintenance({"operation": "request"})

    assert rejected.value.status == 409
    assert rejected.value.code == "emulator_degradation_not_ready"


def test_policy_host_maintenance_records_selected_preventive_trigger(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    policy_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "status": "insufficient_history",
        "automatic_ready": False,
        "reason": "completed-run history is incomplete",
        "candidate_battle_ids": [],
        "baseline_battle_ids": [],
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
            "sample_count": 120,
            "handle_ratio": 8.0,
            "handle_delta": 24_000,
        },
        "host_contention": {"status": "clear"},
        "automatic_triggers": {
            "preventive_handle_ceiling": {
                "status": "ready",
                "ready": True,
                "deferred_by_contention": False,
                "reason": "sustained exact-listener handle ceiling met",
            },
            "severe_in_run_loss": {
                "status": "insufficient",
                "ready": False,
                "reason": "insufficient intervals",
            },
            "completed_run_degradation": {
                "status": "insufficient_history",
                "ready": False,
                "reason": "insufficient history",
            },
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=policy_ready,
        ):
            requested = service.apply_host_maintenance(
                {
                    "operation": "request_automatic",
                    "trigger_kind": "preventive_handle_ceiling",
                    "defer_during_external_contention": True,
                    **listener,
                },
                now=now.timestamp(),
            )
        maintenance = requested["host_maintenance"]["request"]
        assert maintenance["source"] == "windows-control-surface-policy"
        assert maintenance["initiator"] == "automatic_detector"
        assert maintenance["trigger"]["trigger_kind"] == (
            "preventive_handle_ceiling"
        )
        assert maintenance["trigger"]["policy_status"] == "ready"
        assert maintenance["reason"] == (
            "sustained exact-listener handle ceiling met"
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_policy_preventive_request_honors_contention_deferral(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    policy_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "status": "insufficient_history",
        "automatic_ready": False,
        "reason": "completed-run history is incomplete",
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
        },
        "automatic_triggers": {
            "preventive_handle_ceiling": {
                "status": "ready_contended",
                "ready": True,
                "deferred_by_contention": True,
                "reason": "external host contention is present",
            },
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=policy_ready,
        ):
            with pytest.raises(ControlSurfaceRequestError) as rejected:
                service.apply_host_maintenance(
                    {
                        "operation": "request_automatic",
                        "trigger_kind": "preventive_handle_ceiling",
                        "defer_during_external_contention": True,
                        **listener,
                    },
                    now=now.timestamp(),
                )
            requested = service.apply_host_maintenance(
                {
                    "operation": "request_automatic",
                    "trigger_kind": "preventive_handle_ceiling",
                    "defer_during_external_contention": False,
                    **listener,
                },
                now=now.timestamp() + 0.5,
            )
        assert rejected.value.code == "host_contention_deferred"
        assert requested["host_maintenance"]["request"]["state"] == "requested"
        assert requested["host_maintenance"]["request"]["trigger"][
            "defer_during_external_contention"
        ] is False
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_automatic_host_maintenance_rejects_live_listener_mismatch(tmp_path):
    (
        now,
        run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    detector_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "current_run_id": run_id,
        "current_strategy": "farm_t18",
        "status": "automatic_ready",
        "automatic_ready": True,
        "reason": "confirmed",
        "candidate_battle_ids": ["slow-1", "slow-2"],
        "baseline_battle_ids": ["base-1", "base-2", "base-3"],
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
            "sample_count": 120,
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=detector_ready,
        ):
            with pytest.raises(ControlSurfaceRequestError) as rejected:
                service.apply_host_maintenance(
                    {
                        "operation": "request",
                        **{**listener, "process_id": 91},
                    },
                    now=now.timestamp(),
                )
        assert rejected.value.status == 409
        assert rejected.value.code == "emulator_host_identity_mismatch"
        assert service.control_store.status().get("emulator_maintenance") is None
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_automatic_host_maintenance_rejects_seventh_tick_mismatch(tmp_path):
    (
        now,
        run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    detector_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "current_run_id": run_id,
        "current_strategy": "farm_t18",
        "status": "automatic_ready",
        "automatic_ready": True,
        "reason": "confirmed",
        "candidate_battle_ids": ["slow-1", "slow-2"],
        "baseline_battle_ids": ["base-1", "base-2", "base-3"],
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
            "sample_count": 120,
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=detector_ready,
        ):
            with pytest.raises(ControlSurfaceRequestError) as rejected:
                service.apply_host_maintenance(
                    {
                        "operation": "request",
                        **{
                            **listener,
                            "process_started_at": (
                                "2026-08-10T10:00:00.1234568+00:00"
                            ),
                        },
                    },
                    now=now.timestamp(),
                )
        assert rejected.value.code == "emulator_host_identity_mismatch"
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


@pytest.mark.parametrize("operation", ["request", "request_operator"])
def test_host_maintenance_preserves_split_linux_and_windows_ports(
    tmp_path,
    operation,
):
    (
        now,
        run_id,
        owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(
        tmp_path,
        runtime_adb_port=5556,
        windows_adb_port=5555,
    )
    detector_ready = {
        "schema_version": 1,
        "assessed_at": now.isoformat(),
        "current_run_id": run_id,
        "current_strategy": "farm_t18",
        "status": "automatic_ready",
        "automatic_ready": True,
        "reason": "confirmed",
        "candidate_battle_ids": ["slow-1", "slow-2"],
        "baseline_battle_ids": ["base-1", "base-2", "base-3"],
        "host_evidence": {
            "status": "confirmed_growth",
            "identity_scope": "exact_listener_lifetime",
            "listener_identity": listener,
            "sample_count": 120,
        },
    }
    try:
        with patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=detector_ready,
        ):
            requested = service.apply_host_maintenance(
                {"operation": operation, **listener},
                now=now.timestamp(),
            )
        maintenance = requested["host_maintenance"]["request"]
        assert maintenance["runtime"]["adb_target"] == owner["adb_target"]
        assert maintenance["host_target"]["adb_port"] == 5555
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_operator_restart_bypasses_detector_but_keeps_runtime_authority(tmp_path):
    (
        now,
        run_id,
        owner,
        lock_handle,
        service,
        control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    try:
        requested = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp(),
        )
        maintenance = requested["host_maintenance"]["request"]
        assert maintenance["state"] == "requested"
        assert maintenance["initiator"] == "operator"
        assert maintenance["source"] == "windows-control-surface-operator"
        assert maintenance["battle_scope"] == ACTIVE_BATTLE_IDENTITY
        assert maintenance["runtime"] == {
            **owner,
            "state_request_id": control["state_request_id"],
        }
        assert maintenance["trigger"]["request_kind"] == "operator"
        assert {
            key: maintenance["host_target"][key] for key in listener
        } == listener
        assert requested["request"]["disposition"] == "operator_requested"
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_operator_restart_bypasses_same_battle_automatic_suppression(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    try:
        first = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp(),
        )["host_maintenance"]["request"]
        service.control_store.finish_emulator_maintenance(
            first["request_id"],
            disposition="resumed",
            reason="test recovery completed",
            source="test-runtime",
            now=now.timestamp() + 1,
        )

        status = service.status(now=now.timestamp() + 2)
        assert status["emulator_degradation"]["status"] == (
            "already_recovered_this_battle"
        )
        with pytest.raises(ControlSurfaceRequestError) as rejected:
            service.apply_host_maintenance(
                {"operation": "request", **listener},
                now=now.timestamp() + 2,
            )
        assert rejected.value.code == "emulator_degradation_not_ready"

        second = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp() + 2,
        )
        assert second["host_maintenance"]["request"]["request_id"] != (
            first["request_id"]
        )
        assert second["host_maintenance"]["request"]["initiator"] == (
            "operator"
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


@pytest.mark.parametrize(
    ("strategy", "control_state", "expected_code"),
    [
        ("tournament", "RUNNING", "strategy_ineligible"),
        ("farm_t18", "PAUSED", "control_not_running"),
    ],
)
def test_operator_restart_status_rejects_nonfarm_or_paused_authority(
    tmp_path,
    strategy,
    control_state,
    expected_code,
):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(
        tmp_path,
        strategy=strategy,
        control_state=control_state,
    )
    try:
        availability = service.status(now=now.timestamp())["host_maintenance"][
            "operator_restart"
        ]
        assert availability["available"] is False
        assert availability["code"] == expected_code
        with pytest.raises(ControlSurfaceRequestError) as rejected:
            service.apply_host_maintenance(
                {"operation": "request_operator", **listener},
                now=now.timestamp(),
            )
        assert rejected.value.code == expected_code
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


@pytest.mark.parametrize("condition", ["held", "stale"])
def test_operator_restart_rejects_held_or_stale_runtime_authority(
    tmp_path,
    condition,
):
    (
        now,
        run_id,
        _owner,
        lock_handle,
        service,
        _control,
        authority,
        publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    query_time = now.timestamp()
    if condition == "held":
        authority.update_context(
            global_pause=False,
            active_battle=True,
            battle_scope=run_id,
            battle_identity=ACTIVE_BATTLE_IDENTITY,
            primary_state="RUNNING",
            holds=(
                AuthorityHoldState(
                    AuthorityHold.BLOCKING_MODAL_RECOVERY,
                    "test blocking modal",
                ),
            ),
        )
        publisher.publish(
            authority.snapshot(now=query_time),
            now=query_time,
            control_model={
                "schema_version": 1,
                "observation": None,
                "strategy_scope": {"active_battle": "farm_t18"},
            },
        )
    else:
        query_time += 31
    try:
        availability = service.status(now=query_time)["host_maintenance"][
            "operator_restart"
        ]
        assert availability["available"] is False
        assert availability["code"] == "runtime_not_ready"
        with pytest.raises(ControlSurfaceRequestError) as rejected:
            service.apply_host_maintenance(
                {"operation": "request_operator", **listener},
                now=query_time,
            )
        assert rejected.value.code == "runtime_not_ready"
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_operator_restart_ignores_log_scope_rotation(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    _write_current_run_scope(tmp_path, run_id="different-current-run")
    try:
        requested = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp(),
        )
        assert requested["host_maintenance"]["request"]["battle_scope"] == (
            ACTIVE_BATTLE_IDENTITY
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_operator_restart_accepts_gc_farm_strategy_identity(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(
        tmp_path,
        strategy="gc_farm_t19_experiment",
    )
    try:
        availability = service.status(now=now.timestamp())["host_maintenance"][
            "operator_restart"
        ]
        assert availability["available"] is True
        requested = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp(),
        )
        assert requested["host_maintenance"]["request"]["initiator"] == (
            "operator"
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_durable_target_rejects_changed_process_before_acknowledgement(tmp_path):
    (
        now,
        run_id,
        owner,
        lock_handle,
        service,
        control,
        authority,
        publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    try:
        requested = service.apply_host_maintenance(
            {"operation": "request_operator", **listener},
            now=now.timestamp(),
        )
        maintenance = requested["host_maintenance"]["request"]
        bound_runtime = {
            **owner,
            "state_request_id": control["state_request_id"],
        }
        authority.update_context(
            global_pause=False,
            active_battle=True,
            battle_scope=run_id,
            battle_identity=ACTIVE_BATTLE_IDENTITY,
            primary_state="RUNNING",
            holds=(
                AuthorityHoldState(
                    AuthorityHold.EMULATOR_MAINTENANCE,
                    "BlueStacks maintenance owns host and game recovery",
                ),
            ),
        )
        publisher.publish(
            authority.snapshot(now=now.timestamp() + 1),
            now=now.timestamp() + 1,
            control_model={
                "schema_version": 1,
                "observation": None,
                "strategy_scope": {"active_battle": "farm_t18"},
                "emulator_maintenance": {
                    "schema_version": 1,
                    "request_id": maintenance["request_id"],
                    "state": "host_restart_authorized",
                    "runtime": bound_runtime,
                    "battle_scope": ACTIVE_BATTLE_IDENTITY,
                    "high_water_wave": 2_000,
                    "intro_sprint_active": False,
                    "replay_active": False,
                    "exclude_from_degradation": True,
                    "reason": "runtime hold installed",
                    "observed_at": now.isoformat(),
                },
            },
        )
        with pytest.raises(ControlSurfaceRequestError) as rejected:
            service.apply_host_maintenance(
                {
                    "operation": "acknowledge",
                    "request_id": maintenance["request_id"],
                    **{**listener, "process_id": 91},
                },
                now=now.timestamp() + 2,
            )
        assert rejected.value.code == "maintenance_conflict"
        assert service.control_store.status()["emulator_maintenance"][
            "state"
        ] == "requested"
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_pause_before_durable_maintenance_creation_wins(tmp_path):
    (
        now,
        _run_id,
        _owner,
        lock_handle,
        service,
        _control,
        _authority,
        _publisher,
        listener,
    ) = _host_maintenance_context(tmp_path)
    original_request = service.control_store.request_emulator_maintenance

    def pause_then_request(**kwargs):
        service.control_store.set_state("PAUSED", source="test-race")
        return original_request(**kwargs)

    try:
        with patch.object(
            service.control_store,
            "request_emulator_maintenance",
            side_effect=pause_then_request,
        ):
            with pytest.raises(ControlSurfaceRequestError) as rejected:
                service.apply_host_maintenance(
                    {"operation": "request_operator", **listener},
                    now=now.timestamp(),
                )
        assert rejected.value.code == "maintenance_conflict"
        status = service.control_store.status()
        assert status["state"] == "PAUSED"
        assert status.get("emulator_maintenance") is None
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_host_failure_cannot_release_an_acknowledged_restart(tmp_path):
    service = _service(tmp_path)
    enabled = service.control_store.set_state("RUNNING", source="test")
    request = service.control_store.request_emulator_maintenance(
        reason="confirmed degradation",
        source="test",
        runtime={
            "runtime_id": "runtime-1",
            "pid": os.getpid(),
            "adb_target": "localhost:5555",
            "target_generation": 3,
            "state_request_id": enabled["state_request_id"],
        },
        battle_scope="run-1",
        host_target={
            "host_id": "WINDOWS-HOST",
            "adb_port": 5555,
            "process_id": 90,
            "process_started_at": "2026-08-10T10:00:00+00:00",
            "executable_path": (
                r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
            ),
            "instance_name": "Nougat32",
            "observed_at": "2026-08-10T10:00:30+00:00",
        },
    )
    service.control_store.acknowledge_emulator_maintenance_host(
        request["request_id"],
        host_ack={
            "host_id": "WINDOWS-HOST",
            "adb_port": 5555,
            "process_id": 90,
            "process_started_at": "2026-08-10T10:00:00+00:00",
            "executable_path": (
                r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
            ),
            "instance_name": "Nougat32",
            "observed_at": "2026-08-10T10:01:00+00:00",
        },
    )

    with pytest.raises(ControlSurfaceRequestError) as rejected:
        service.apply_host_maintenance(
            {
                "operation": "fail",
                "request_id": request["request_id"],
                "reason": "Windows result uncertain",
            }
        )

    assert rejected.value.status == 409
    assert rejected.value.code == "maintenance_reconciliation_required"
    assert service.control_store.status()["emulator_maintenance"]["state"] == (
        "host_acknowledged"
    )


def test_interactive_development_request_waits_for_exclusive_runtime_hold(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="validation-run",
        primary_state="RUNNING",
        holds=(
            AuthorityHoldState(
                AuthorityHold.EXCLUSIVE_VALIDATION,
                "validation terminal persistence owns the runtime boundary",
            ),
        ),
    )
    owner = {
        "runtime_id": "runtime-validation-hold",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
    }
    RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    ).publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
    )
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
    try:
        with pytest.raises(ControlSurfaceRequestError) as busy:
            service.apply_interactive_development_lease(
                {
                    "operation": "request",
                    "owner_label": "must wait for validation",
                },
                now=now.timestamp(),
            )

        assert busy.value.status == 409
        assert busy.value.code == "busy"
        assert "exclusive_validation" in str(busy.value)
        assert (
            service.status(now=now.timestamp())["interactive_development_lease"]
            ["request"]
            is None
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_interactive_development_status_separates_request_and_fresh_ack(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=True,
        battle_scope="run-lease",
        primary_state="RUNNING",
    )
    owner = {
        "runtime_id": "runtime-lease",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
    }
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    )
    publisher.publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
    )
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
    try:
        requested = service.apply_interactive_development_lease(
            {
                "operation": "request",
                "owner_label": "interactive lease status test",
            },
            now=now.timestamp(),
        )
        lease = requested["interactive_development_lease"]["request"]
        assert lease["runtime"] == owner
        assert lease["expires_at"] == (
            now + timedelta(seconds=INTERACTIVE_DEVELOPMENT_LEASE_TTL_SECONDS)
        ).isoformat(timespec="seconds")
        assert lease["starting_evidence"] == {
            "screen_state": "RUNNING",
            "battle_active": True,
            "battle_scope": "run-lease",
            "observed_at": now.isoformat(timespec="microseconds"),
        }
        assert requested["interactive_development_lease"][
            "runtime_acknowledgement"
        ] is None
        assert requested["interactive_development_lease"]["active"] is False

        with pytest.raises(ControlSurfaceRequestError) as busy:
            service.apply_interactive_development_lease(
                {"operation": "request", "owner_label": "conflict"},
                now=now.timestamp() + 1,
            )
        assert busy.value.status == 409
        assert busy.value.code == "busy"

        before_heartbeat = (tmp_path / "logs" / "actions.log").read_text(
            encoding="utf-8"
        )
        heartbeat = service.apply_interactive_development_lease(
            {"operation": "heartbeat", "lease_id": lease["lease_id"]},
            now=now.timestamp() + 2,
        )
        after_heartbeat = (tmp_path / "logs" / "actions.log").read_text(
            encoding="utf-8"
        )
        assert heartbeat["operation"]["operation"] == "heartbeat"
        assert after_heartbeat == before_heartbeat

        live_request = heartbeat["interactive_development_lease"]["request"]
        authority.update_context(
            global_pause=False,
            active_battle=True,
            battle_scope="run-lease",
            primary_state="RUNNING",
            holds=(
                AuthorityHoldState(
                    AuthorityHold.EXTERNAL_DEVELOPMENT,
                    "interactive development owns the cooperative input window",
                ),
            ),
        )
        acknowledged_at = (
            now + timedelta(seconds=3)
        ).isoformat(timespec="seconds")
        acknowledgement = {
            "schema_version": 1,
            "lease_id": lease["lease_id"],
            "owner_label": lease["owner_label"],
            "state": "active",
            "requested_at": live_request["requested_at"],
            "heartbeat_at": live_request["heartbeat_at"],
            "expires_at": live_request["expires_at"],
            "runtime": owner,
            "hold_installed_at": acknowledged_at,
            "acknowledged_at": acknowledged_at,
            "activated_at": acknowledged_at,
            "updated_at": acknowledged_at,
            "starting_evidence": {
                "screen_state": "RUNNING",
                "battle_active": True,
                "battle_scope": "run-lease",
                "observed_at": acknowledged_at,
            },
        }
        publisher.publish(
            authority.snapshot(now=now.timestamp() + 3),
            now=now.timestamp() + 3,
            interactive_development_lease=acknowledgement,
        )
        active_status = service.status(now=now.timestamp() + 3)
        active = active_status["interactive_development_lease"]
        assert active["request"]["request_state"] == "requested"
        assert active["runtime_acknowledgement"]["state"] == "active"
        assert active["acknowledgement_fresh"] is True
        assert active["owner_matches_request"] is True
        assert active["external_hold_installed"] is True
        assert active["active"] is True
        helper_authority = validate_active_lease_status(
            active_status,
            lease_id=lease["lease_id"],
        )
        assert helper_authority.adb_target == "localhost:5555"
        assert helper_authority.runtime_pid == os.getpid()

        stale = service.status(now=now.timestamp() + 40)[
            "interactive_development_lease"
        ]
        assert stale["acknowledgement_fresh"] is False
        assert stale["active"] is False

        released = service.apply_interactive_development_lease(
            {"operation": "release", "lease_id": lease["lease_id"]},
            now=now.timestamp() + 4,
        )["interactive_development_lease"]
        assert released["request"]["request_state"] == "release_requested"
        assert released["runtime_acknowledgement"]["state"] == "active"
        assert released["active"] is False
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_interactive_development_owned_battle_is_preclaimed_at_home_new(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="HOME_SCREEN")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=False,
        battle_scope="run-owned",
        primary_state="HOME_SCREEN",
    )
    owner = {
        "runtime_id": "runtime-owned",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
    }
    observation = {
        "schema_version": 1,
        "observation_id": "runtime-owned:1",
        "observed_at": now.isoformat(timespec="microseconds"),
        "primary_state": "HOME_SCREEN",
        "home_battle_control": "NEW_BATTLE",
        "game_state": "home_new_battle",
        "active_battle": False,
        "activity_scope_run_id": "run-owned",
        "target_generation": 7,
    }
    RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    ).publish(
        authority.snapshot(now=now.timestamp()),
        now=now.timestamp(),
        control_model={
            "schema_version": 1,
            "observation": observation,
        },
    )
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
    try:
        response = service.apply_interactive_development_lease(
            {
                "operation": "request",
                "owner_label": "owned mapping battle",
                "owned_battle_start": True,
            },
            now=now.timestamp(),
        )

        lease = response["interactive_development_lease"]["request"]
        assert lease["owned_battle_start"] is True
        assert lease["starting_evidence"] == {
            "screen_state": "HOME_SCREEN",
            "battle_active": False,
            "battle_scope": "run-owned",
            "observed_at": now.isoformat(timespec="microseconds"),
            "home_battle_control": "NEW_BATTLE",
            "target_generation": 7,
        }
        assert "interactive_development_owned_battle_v1" in response[
            "capabilities"
        ]
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_interactive_development_heartbeat_rejects_stale_or_wrong_lease(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=False,
        battle_scope="run-home",
        primary_state="HOME_SCREEN",
    )
    owner = {
        "runtime_id": "runtime-heartbeat",
        "pid": os.getpid(),
        "adb_target": "localhost:5555",
    }
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner=owner,
        stale_after_seconds=30,
    )
    publisher.publish(authority.snapshot(), now=now.timestamp())
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
    try:
        response = service.apply_interactive_development_lease(
            {"operation": "request", "owner_label": "heartbeat test"},
            now=now.timestamp(),
        )
        lease_id = response["operation"]["lease_id"]
        with pytest.raises(ControlSurfaceRequestError) as wrong_heartbeat:
            service.apply_interactive_development_lease(
                {"operation": "heartbeat", "lease_id": "0" * 32},
                now=now.timestamp() + 1,
            )
        assert wrong_heartbeat.value.status == 409
        with pytest.raises(ControlSurfaceRequestError) as wrong_release:
            service.apply_interactive_development_lease(
                {"operation": "release", "lease_id": "0" * 32},
                now=now.timestamp() + 1,
            )
        assert wrong_release.value.status == 409

        with pytest.raises(ControlSurfaceRequestError) as stale:
            service.apply_interactive_development_lease(
                {"operation": "heartbeat", "lease_id": lease_id},
                now=now.timestamp() + 31,
            )
        assert stale.value.status == 409
        assert "no longer fresh" in str(stale.value)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_http_interactive_development_endpoint_returns_busy_and_id_errors(
    tmp_path,
):
    now = datetime.now().astimezone().replace(microsecond=0)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    authority = RuntimeActionAuthority()
    authority.update_context(
        global_pause=False,
        active_battle=False,
        battle_scope="run-http",
        primary_state="HOME_SCREEN",
    )
    publisher = RuntimeActionAuthorityPublisher(
        tmp_path / "logs" / "strategy_action_gate.json",
        owner={
            "runtime_id": "runtime-http",
            "pid": os.getpid(),
            "adb_target": "localhost:5555",
        },
    )
    publisher.publish(authority.snapshot(), now=now.timestamp())
    service = _service(tmp_path)
    service.control_store.set_state("RUNNING", source="test")
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

    def post(payload):
        body = json.dumps(payload)
        connection.request(
            "POST",
            "/api/v1/interactive-development-lease",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    try:
        status, requested = post(
            {"operation": "request", "owner_label": "HTTP lease test"}
        )
        assert status == 200
        lease_id = requested["operation"]["lease_id"]
        assert requested["interactive_development_lease"]["active"] is False

        status, busy = post(
            {"operation": "request", "owner_label": "HTTP conflict"}
        )
        assert status == 409
        assert busy["code"] == "busy"

        status, wrong_heartbeat = post(
            {"operation": "heartbeat", "lease_id": "0" * 32}
        )
        assert status == 409
        assert "does not match" in wrong_heartbeat["error"]
        status, wrong_release = post(
            {"operation": "release", "lease_id": "0" * 32}
        )
        assert status == 409
        assert "does not match" in wrong_release["error"]

        status, heartbeat = post(
            {"operation": "heartbeat", "lease_id": lease_id}
        )
        assert status == 200
        assert heartbeat["operation"]["operation"] == "heartbeat"
        status, release = post(
            {"operation": "release", "lease_id": lease_id}
        )
        assert status == 200
        assert release["interactive_development_lease"]["request"][
            "request_state"
        ] == "release_requested"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_status_reads_concise_heartbeat_with_paired_diagnostic_detail(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        f"[STATUS {timestamp}] State=RUNNING/PAUSED | Wave=521 | "
        "Coins/min=1.3T | Speed=x5.0\n"
        f"[DEBUG {timestamp}] [STATUS_DETAIL] State=RUNNING/PAUSED | Wave=521 | "
        "Coins/min=1.3T | Speed=x5.0 | Menu=UW_MENU | Secondary=[PERKS] | "
        "Overlays=[MENU_OPEN]\n",
        encoding="utf-8",
    )

    service = _service(tmp_path)
    status = service.status(now=now.timestamp())

    assert status["observation"] == {
        "state": "RUNNING",
        "paused": True,
        "state_label": "RUNNING/PAUSED",
        "wave": 521,
        "coins_per_minute": "1.3T",
        "game_speed": 5.0,
        "menu": "UW_MENU",
        "secondary": ["PERKS"],
        "overlays": ["MENU_OPEN"],
        "observed_at": now.isoformat(timespec="seconds"),
        "age_seconds": 0,
        "stale": False,
    }
    assert status["prior_transition"] is None
    operational = service.activity(
        levels=["STATUS", "ACTION", "INFO", "WARN", "ERROR", "FAIL"],
    )
    assert [entry["level"] for entry in operational["items"]] == ["STATUS"]
    assert [entry["level"] for entry in service.activity()["items"]] == [
        "STATUS",
        "DEBUG",
    ]


def test_status_exposes_prior_meaningful_state_transition(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    home_timestamp = (now - timedelta(seconds=120)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    earlier_running_timestamp = (now - timedelta(seconds=60)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        f"[STATUS {home_timestamp}] State=HOME_SCREEN | Wave=— | Coins/min=—\n"
        f"[STATUS {earlier_running_timestamp}] State=RUNNING | Wave=10 | "
        "Coins/min=1.0T\n"
        f"[STATUS {timestamp}] State=RUNNING | Wave=20 | Coins/min=1.2T\n",
        encoding="utf-8",
    )

    status = _service(tmp_path).status(now=now.timestamp())

    assert status["observation"]["state_label"] == "RUNNING"
    assert status["observation"]["wave"] == 20
    assert status["prior_transition"] == {
        "state": "HOME_SCREEN",
        "paused": False,
        "state_label": "HOME_SCREEN",
        "wave": None,
        "coins_per_minute": None,
        "game_speed": None,
        "menu": None,
        "secondary": [],
        "overlays": [],
        "observed_at": (
            now - timedelta(seconds=120)
        ).isoformat(timespec="seconds"),
        "age_seconds": 120,
        "stale": False,
    }


def test_runtime_evidence_exposes_clean_release_metadata(tmp_path):
    released_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    lock_path = tmp_path / "logs" / "automation-localhost_5555.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": None,
                "released_at": released_at,
                "state": "released",
                "target": "localhost:5555",
            }
        ),
        encoding="utf-8",
    )

    runtime = _service(tmp_path)._runtime_evidence()

    assert not runtime["active"]
    assert runtime["instances"] == [
        {
            "active": False,
            "file": "automation-localhost_5555.lock",
            "lock_held": False,
            "metadata_state": "released",
            "pid": None,
            "pid_alive": None,
            "released_at": released_at,
            "runtime_id": None,
            "started_at": None,
            "target": "localhost:5555",
            "target_generation": None,
        }
    ]


def test_battle_list_is_compact_and_full_record_requires_exact_id(tmp_path):
    _write_battle(tmp_path)
    service = _service(tmp_path)

    listing = service.battles(limit=10)
    assert listing["total"] == 1
    assert listing["errors"] == []
    assert listing["items"][0] == {
        "battle_id": "Battle20260719T101126-0700",
        "captured_at": "2026-07-19T10:11:26-07:00",
        "strategy": "farm_t18",
        "battle_type": "farm",
        "battle_type_label": "Farm",
        "battle_type_confidence": "high",
        "profile": "farm",
        "tier": 18,
        "wave": 520,
        "killed_by": None,
        "league": None,
        "rank": None,
        "game_time": "26m 35s",
        "real_time": "6m 16s",
        "coins_earned": "1.2T",
        "coins_per_hour": "11.5T",
        "cells_earned": "797",
        "cells_per_hour": "7.62K",
        "derived": {"effective_game_speed": 4.242},
        "quality": {"valid": True, "warnings": []},
    }
    assert service.battle("Battle20260719T101126-0700")["schema_version"] == 2
    with pytest.raises(ControlSurfaceRequestError) as exc_info:
        service.battle("../automation_ctl")
    assert exc_info.value.status == 404


def test_completed_battle_discard_moves_pair_then_purges_after_deadline(tmp_path):
    battle_id = "Battle20260719T101126-0700"
    json_path = _write_battle(tmp_path, battle_id)
    markdown_path = json_path.with_suffix(".md")
    markdown_path.write_text(f"# {battle_id}\n", encoding="utf-8")
    service = ControlSurfaceService(
        repository_root=tmp_path,
        discarded_battle_retention_days=30,
    )
    discarded_at = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    response = service.discard_battle(battle_id, now=discarded_at)

    assert response["battle_id"] == battle_id
    assert response["files"] == [json_path.name, markdown_path.name]
    assert not json_path.exists()
    assert not markdown_path.exists()
    package = tmp_path / response["quarantine_path"]
    assert package.is_dir()
    assert (package / json_path.name).is_file()
    assert (package / markdown_path.name).is_file()
    metadata = json.loads((package / "discard.json").read_text(encoding="utf-8"))
    assert metadata["battle_id"] == battle_id
    assert service.battles()["total"] == 0
    assert service.purge_expired_discarded_battles(
        now=discarded_at + timedelta(days=29)
    ) == 0
    assert package.exists()
    assert service.purge_expired_discarded_battles(
        now=discarded_at + timedelta(days=30)
    ) == 1
    assert not package.exists()


def test_completed_battle_discard_rejects_nonexact_or_missing_ids(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(ControlSurfaceRequestError) as invalid:
        service.discard_battle("../automation_ctl")
    assert invalid.value.status == 404

    with pytest.raises(ControlSurfaceRequestError) as missing:
        service.discard_battle("Battle20260719T101126-0700")
    assert missing.value.status == 404


def test_completed_battles_include_tournament_records_and_terminal_classification(tmp_path):
    _write_battle(tmp_path)
    tournament_id = "Tournament20260720T061923-0700"
    tournament = {
        "schema_version": 1,
        "tournament_id": tournament_id,
        "captured_at": "2026-07-20T06:19:23-07:00",
        "strategy": "tournament",
        "run_configuration": {"profile": "tournament"},
        "runtime": {},
        "summary": {
            "fields": {
                "league": {"raw": "Legend League", "value": "Legend League"},
                "wave": {"raw": "2028", "value": 2028},
                "rank": {"raw": "4", "value": 4},
            }
        },
        "detailed_stats": {
            "sections": [
                {
                    "key": "battle_report",
                    "rows": [
                        {"key": "tier", "value_raw": "17+", "value": 17},
                        {"key": "wave", "value_raw": "2028", "value": 2028},
                        {"key": "coins_per_hour", "value_raw": "12.68T"},
                        {"key": "cells_per_hour", "value_raw": "19.95K"},
                    ],
                }
            ]
        },
        "quality": {"valid": True, "warnings": []},
    }
    path = tmp_path / "logs" / "tournaments" / f"{tournament_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(tournament), encoding="utf-8")

    service = _service(tmp_path)
    listing = service.battles(limit=10)

    assert listing["total"] == 2
    assert listing["items"][0]["battle_id"] == tournament_id
    assert listing["items"][0]["battle_type"] == "tournament"
    assert listing["items"][0]["league"] == "Legend League"
    assert listing["items"][0]["tier"] == 17
    assert service.battle(tournament_id)["tournament_id"] == tournament_id


def test_battle_list_reports_terminal_tier_for_ambiguous_no_strategy_run(tmp_path):
    battle_id = "Battle20260720T071923-0700"
    record = {
        "schema_version": 2,
        "battle_id": battle_id,
        "captured_at": "2026-07-20T07:19:23-07:00",
        "strategy": None,
        "runtime": {"terminal_state": "GAME_OVER"},
        "game_stats": {
            "fields": {"tier": {"raw": "20", "value": 20}},
        },
        "more_stats": {"sections": []},
        "quality": {"valid": False, "warnings": []},
    }
    path = tmp_path / "logs" / "battles" / f"{battle_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    item = _service(tmp_path).battles(limit=10)["items"][0]

    assert item["battle_type"] == "unknown"
    assert item["tier"] == 20


def test_control_requests_are_allowlisted_and_audited(tmp_path):
    service = _service(tmp_path)
    lock_handle = _fresh_runtime_lock(tmp_path, state="RUNNING")
    response = service.apply_control({"action": "pause", "minutes": 15})
    lock_handle.close()
    assert response["request"] == {
        "accepted": True,
        "action": "pause",
        "disposition": "requested",
    }
    control = service.control_store.read()
    assert control["state"] == "PAUSED"
    assert control["updated_by"] == "control-surface"
    assert control["resume_at"] > datetime.now().timestamp()
    assert "[CONTROL_SURFACE] Requested PAUSED for 15 minutes" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")

    legacy_mode_response = service.apply_control(
        {"action": "mode", "mode": "RETRY"}
    )
    assert legacy_mode_response["control"]["mode"] == "NEXT_BATTLE"
    assert "[CONTROL_SURFACE] Set When this battle ends to NEXT_BATTLE" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")

    mode_response = service.apply_control({"action": "mode", "mode": "HOME"})
    assert mode_response["control"]["mode"] == "HOME"
    assert service.status()["control"]["mode"] == "HOME"

    speed_response = service.apply_control(
        {"action": "game_speed", "target": 4.5}
    )
    assert speed_response["control"]["game_speed_target"] == 4.5
    assert service.status()["control"]["game_speed_target"] == 4.5
    assert "[CONTROL_SURFACE] Requested game speed target x4.5" in (
        tmp_path / "logs" / "actions.log"
    ).read_text(encoding="utf-8")

    with pytest.raises(ControlSurfaceRequestError):
        service.apply_control({"action": "tap", "x": 10, "y": 10})
    with pytest.raises(ControlSurfaceRequestError):
        service.apply_control({"action": "pause", "minutes": 0})
    with pytest.raises(ControlSurfaceRequestError):
        service.apply_control({"action": "game_speed", "target": 4.2})


def test_control_surface_resolves_only_an_offered_pending_gate_choice(tmp_path):
    service = _service(tmp_path)
    directive = service.control_store.publish_gate_decision(
        strategy="farm_t18",
        phase="home_setup",
        check_id="bots_preset",
        reason="Farm preset requires 240 medals",
        expected="Farm",
        options=build_gate_decision_options(
            "bots_preset",
            [{"id": "flame", "label": "Continue with Flame", "value": "Flame"}],
        ),
    )

    response = service.apply_control(
        {
            "action": "resolve_gate",
            "request_id": directive["request_id"],
            "decision_id": "flame",
        }
    )

    resolved = service.control_store.status()["gate_decision"]
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["decision_id"] == "flame"
    assert response["request"] == {
        "accepted": True,
        "action": "resolve_gate",
        "request_id": directive["request_id"],
        "decision_id": "flame",
    }

    with pytest.raises(ControlSurfaceRequestError, match="no longer pending"):
        service.apply_control(
            {
                "action": "resolve_gate",
                "request_id": directive["request_id"],
                "decision_id": "retry",
            }
        )


def test_control_surface_starts_or_cancels_only_current_ready_tournament(
    tmp_path,
):
    service = _service(tmp_path)
    ready = _ready_tournament_launch(service)
    service.control_store.set_state("RUNNING", source="test")
    lock_handle = _fresh_runtime_lock(tmp_path, state="HOME_SCREEN")
    try:
        response = service.apply_control(
            {
                "action": "resolve_tournament_launch",
                "request_id": ready["request_id"],
                "decision": "start",
            }
        )
    finally:
        lock_handle.close()

    assert response["request"] == {
        "accepted": True,
        "action": "resolve_tournament_launch",
        "request_id": ready["request_id"],
        "decision_id": "start",
    }
    receipt = service.control_store.status()["exclusive_validation"][
        "receipts"
    ][ready["request_id"]]
    assert receipt["launch"]["status"] == "requested"

    other_root = tmp_path / "cancel"
    cancel_service = _service(other_root)
    cancel_ready = _ready_tournament_launch(cancel_service)
    cancelled = cancel_service.apply_control(
        {
            "action": "resolve_tournament_launch",
            "request_id": cancel_ready["request_id"],
            "decision": "cancel",
        }
    )
    assert cancelled["request"]["decision_id"] == "cancel"
    assert cancel_service.control_store.status()["exclusive_validation"][
        "receipts"
    ][cancel_ready["request_id"]]["launch"]["status"] == "cancelled"


def test_control_surface_rejects_start_without_fresh_safe_runtime(tmp_path):
    service = _service(tmp_path)
    ready = _ready_tournament_launch(service)
    service.control_store.set_state("RUNNING", source="test")

    with pytest.raises(
        ControlSurfaceRequestError,
        match="active automation runtime",
    ):
        service.apply_control(
            {
                "action": "resolve_tournament_launch",
                "request_id": ready["request_id"],
                "decision": "start",
            }
        )

    assert service.control_store.status()["exclusive_validation"]["receipts"][
        ready["request_id"]
    ]["launch"]["status"] == "awaiting_operator"


def test_browser_client_exposes_tournament_start_cancel_and_reminder():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="startTournamentLaunchButton"' in html
    assert 'id="cancelTournamentLaunchButton"' in html
    assert "Target Priority reminder" in html
    assert 'action: "resolve_tournament_launch"' in script
    assert 'resolveTournamentLaunch("start")' in script
    assert 'resolveTournamentLaunch("cancel")' in script


def test_native_battle_history_surfaces_partial_save_metric_claims():
    source = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
        / "BattleHistoryWindow.xaml.cs"
    ).read_text(encoding="utf-8")

    assert '"Save-backed metric status"' in source
    assert '"Save-backed capability status"' in source
    assert 'TryGetProperty("terminal_relation"' in source
    assert '"Terminal relation"' in source
    assert '"Terminal save-backed claim status"' in source
    assert 'missingProperty: "missing"' in source
    assert 'TryGetProperty("metric_conflicts"' in source
    assert 'TryGetProperty("conflicts"' in source
    assert 'component.NameEquals("economy")' not in source
    assert "no later in-battle save captured the active timer" in source


def test_browser_activity_defaults_to_operational_narrative_levels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    native_xaml = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
        / "MainWindow.xaml"
    ).read_text(encoding="utf-8")
    native_compatibility = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
        / "ControlSurfaceCompatibility.cs"
    ).read_text(encoding="utf-8")
    native_code = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
        / "MainWindow.xaml.cs"
    ).read_text(encoding="utf-8")
    native_models = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
        / "Models.cs"
    ).read_text(encoding="utf-8")

    assert 'id="priorTransition"' in html
    assert (
        "/api/v1/activity?limit=70&levels=ACTION,RESULT,WARN,ERROR,FAIL"
        in script
    )
    assert "levels=STATUS,ACTION,INFO" not in script
    assert (
        'Content="Operational" Tag="ACTION,RESULT,WARN,ERROR,FAIL"'
        in native_xaml
    )
    assert (
        'Content="Diagnostics" Tag="INPUT,DEBUG,MATCH,STATE"'
        in native_xaml
    )
    assert 'Content="Current run" Tag="current_run"' in native_xaml
    assert 'Content="All recent" Tag="all"' in native_xaml
    assert 'Content="Clear view"' in native_xaml
    assert 'Text="CURRENT STATUS"' in native_xaml
    assert 'Text="PREVIOUS GAME SCREEN"' in native_xaml
    assert 'id="gameSpeedTargetSelect"' in html
    assert 'Content="x6.3 — Maximum available"' in native_xaml
    assert 'Content="Continue automatically"' in native_xaml
    assert 'Tag="NEXT_BATTLE"' in native_xaml
    assert 'Content="Return / stay Home"' in native_xaml
    assert '<option value="NEXT_BATTLE">Continue automatically</option>' in html
    assert '<option value="HOME">Return to / stay Home</option>' in html
    assert "When this battle ends" in html
    assert 'id="terminalPolicyStatus"' in html
    assert "RetryModeButton" not in native_xaml
    assert "MinimumServerRevision = 47" in native_compatibility
    assert '"active_run_metrics_v1"' in native_compatibility
    assert "active_run_metrics_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '"emulator_host_selection_v1"' in native_compatibility
    assert "emulator_host_selection_v1" in CONTROL_SURFACE_CAPABILITIES
    assert 'x:Name="UseThisEmulatorButton"' in native_xaml
    assert 'Click="UseThisEmulator_Click"' in native_xaml
    assert 'JsonPropertyName("emulator_location")' in native_models
    assert '"bluestacks_maintenance_v1"' not in native_compatibility
    assert '"bluestacks_maintenance_v2"' in native_compatibility
    assert '"bluestacks_operator_restart_v1"' in native_compatibility
    assert (
        '"bluestacks_listener_lifetime_telemetry_v1"'
        in native_compatibility
    )
    assert '"bluestacks_maintenance_policy_v1"' in native_compatibility
    assert '"strategy_aware_attach_v1"' in native_compatibility
    assert '"confirmed_local_mapping_status_v2"' in native_compatibility
    assert "confirmed_local_mapping_status_v2" in CONTROL_SURFACE_CAPABILITIES
    assert '"save_mapping_review_status_v2"' in native_compatibility
    assert "save_mapping_review_status_v2" in CONTROL_SURFACE_CAPABILITIES
    assert '"save_mapping_staged_candidate_v1"' in native_compatibility
    assert "save_mapping_staged_candidate_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '"save_mapping_candidate_disposition_v1"' in native_compatibility
    assert "save_mapping_candidate_disposition_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '"save_mapping_automatic_promotion_v1"' in native_compatibility
    assert "save_mapping_automatic_promotion_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '"save_mapping_machine_verification_v1"' in native_compatibility
    assert "save_mapping_machine_verification_v1" in CONTROL_SURFACE_CAPABILITIES
    assert 'id="confirmedLocalMappingAlert"' in html
    assert 'x:Name="ConfirmedLocalMappingBanner"' in native_xaml
    assert 'JsonPropertyName("confirmed_local_mappings")' in native_models
    assert 'x:Name="ReviewSaveMappingsButton"' in native_xaml
    assert 'Header="Save mapping integration…"' in native_xaml
    assert "RenderConfirmedLocalMappings(status.ConfirmedLocalMappings)" in native_code
    assert '"better_control_model_v2"' in native_compatibility
    assert '"runtime_control_acknowledgements_v1"' in native_compatibility
    assert "ResolveStrategyScope(" in native_compatibility
    assert (
        "ControlSurfaceCompatibility.ResolveStrategyScope(" in native_code
    )
    assert "better_control_model_v1" in CONTROL_SURFACE_CAPABILITIES
    assert "better_control_model_v2" in CONTROL_SURFACE_CAPABILITIES
    assert '"current_battle_perks_v1"' in native_compatibility
    assert "current_battle_perks_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '<TabItem x:Name="PerksTab" Header="Perks" Tag="perks">' in native_xaml
    assert 'x:Name="CurrentPerksGrid"' in native_xaml
    assert 'JsonPropertyName("current_battle_perks")' in native_models
    assert "RenderCurrentBattlePerks(status.CurrentBattlePerks)" in native_code
    assert '"save_backed_setup_capture_v2"' in native_compatibility
    assert "save_backed_setup_capture_v1" in CONTROL_SURFACE_CAPABILITIES
    assert "save_backed_setup_capture_v2" in CONTROL_SURFACE_CAPABILITIES
    assert '"terminal_dispositions_v2"' in native_compatibility
    assert "terminal_dispositions_v2" in CONTROL_SURFACE_CAPABILITIES
    assert '"managed_custom_module_presets_v1"' in native_compatibility
    assert '"strategy_authoring_local_loadout_editors_v1"' in native_compatibility
    assert '"strategy_authoring_preset_local_copy_v1"' in native_compatibility
    assert "strategy_authoring_preset_local_copy_v1" in CONTROL_SURFACE_CAPABILITIES
    assert '"strategy_revision_history_v1"' in native_compatibility
    assert '"strategy_action_gate_v1"' in native_compatibility
    assert 'x:Name="StrategyActionGateBanner"' in native_xaml
    assert (
        "Strategy actions blocked — observation and safe collectors remain active."
        in native_xaml
    )
    assert 'x:Name="StrategyActionGateReasonText"' in native_xaml
    assert 'x:Name="StrategyActionGateChecksText"' in native_xaml
    assert 'x:Name="StrategyActionGateCollectorsText"' in native_xaml
    assert "{ Available: true, Active: true, Stale: false }" in native_code
    assert (
        "DirectiveText.Text = FormatActionAuthority("
        in native_code
    )
    assert 'JsonPropertyName("strategy_action_gate")' in native_models
    assert 'JsonPropertyName("control_model")' in native_models
    assert 'JsonPropertyName("failed_check_ids")' in native_models
    assert 'JsonPropertyName("allowed_auxiliary_collectors")' in native_models
    assert '"current_run_activity_scope"' in native_compatibility
    assert '"game_speed_target"' in native_compatibility
    assert '"host_performance_telemetry_v1"' in native_compatibility
    assert '"host_performance_gpu_v1"' in native_compatibility
    assert '"host_performance_process_attribution_v1"' in native_compatibility
    assert (
        "host_performance_process_attribution_v1"
        in CONTROL_SURFACE_CAPABILITIES
    )
    assert '"automatic_battle_attachment"' not in native_compatibility


def test_native_dashboard_uses_stable_full_width_pages_and_bounded_system_views():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    native_models = (native_root / "Models.cs").read_text(
        encoding="utf-8"
    )

    for page in (
        '<TabItem x:Name="OverviewTab" Header="Overview" Tag="overview">',
        '<TabItem x:Name="ActivityTab" Header="Activity" Tag="activity">',
        '<TabItem x:Name="PerksTab" Header="Perks" Tag="perks">',
        '<TabItem x:Name="SystemTab" Header="System" Tag="system">',
    ):
        assert page in native_xaml
    for system_page in (
        '<TabItem Header="Services" Tag="services">',
        '<TabItem Header="Connections" Tag="connections">',
        '<TabItem Header="Diagnostics" Tag="diagnostics">',
    ):
        assert system_page in native_xaml

    assert 'Header="_View"' in native_xaml
    assert 'Header="_Tools"' in native_xaml
    assert 'Header="_Preferences"' in native_xaml
    assert 'x:Name="SidebarColumn"' not in native_xaml
    assert 'x:Name="ProcessStateText"' in native_xaml
    assert 'x:Name="DirectiveRequestText"' in native_xaml
    assert 'x:Name="StrategyScopeText"' in native_xaml
    assert 'x:Name="TargetSpeedText"' in native_xaml
    assert 'x:Name="LinuxApiServiceStatusText"' in native_xaml
    assert 'x:Name="ConnectionText"' in native_xaml
    assert 'x:Name="ApiTunnelTopStatusText"' in native_xaml
    assert 'x:Name="AdbTunnelTopStatusText"' in native_xaml

    assert 'private const string OverviewPageId = "overview";' in native_code
    assert 'private const string SystemPageId = "system";' in native_code
    assert "layout.SidebarTabIndex switch" in native_code
    assert "SelectedPageId(SidebarTabs, OverviewPageId)" in native_code
    assert "ReferenceEquals(SidebarTabs.SelectedItem, ActivityTab)" in native_code
    assert 'public string DashboardPage { get; set; } = "";' in native_models
    assert 'public string SystemPage { get; set; } = "";' in native_models


def test_native_preferences_are_bounded_and_adb_drafts_survive_status_polling():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    preferences_xaml = (native_root / "PreferencesWindow.xaml").read_text(
        encoding="utf-8"
    )
    preferences_code = (native_root / "PreferencesWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    native_models = (native_root / "Models.cs").read_text(
        encoding="utf-8"
    )

    assert 'Title="Preferences"' in preferences_xaml
    assert 'Text="CONTROL API"' in preferences_xaml
    assert 'Text="SSH AND FORWARDING DEFAULTS"' in preferences_xaml
    assert 'Text="PRESENTATION AND LOCAL SAMPLING"' in preferences_xaml
    for field in (
        "BaseUrlBox",
        "TokenBox",
        "SshDestinationBox",
        "LocalTunnelPortBox",
        "RemoteApiPortBox",
        "WindowsBlueStacksAdbPortBox",
        "LinuxAdbForwardPortBox",
    ):
        assert f'x:Name="{field}"' in preferences_xaml
        assert f'x:Name="{field}"' not in native_xaml
    assert "PreferencesResult" in preferences_code
    for recovery_option in (
        "BlueStacksAutomaticRecoveryBox",
        "BlueStacksPreventiveHandleRecoveryBox",
        "BlueStacksInRunPerformanceRecoveryBox",
        "BlueStacksCompletedRunRecoveryBox",
        "BlueStacksDeferDuringExternalContentionBox",
    ):
        assert f'x:Name="{recovery_option}"' in preferences_xaml
    assert 'x:Name="BlueStacksAutomaticPolicyText"' in native_xaml
    assert "would trigger (disabled)" in native_code
    assert 'operation = "request_automatic"' in (
        native_root / "BlueStacksMaintenanceCoordinator.cs"
    ).read_text(encoding="utf-8")
    assert "requireDestination: false" in preferences_code
    assert "public string Token" not in native_models
    assert 'private string _apiToken = "";' in native_code
    assert "Command = TunnelHostCommand.Configure" in native_code
    assert "Preferences changes saved defaults only." in native_xaml

    for target_field in (
        "ConfiguredAdbTargetText",
        "RequestedAdbTargetText",
        "ActiveAdbTargetText",
        "AdbDraftStateText",
        "RevertAdbPortButton",
    ):
        assert f'x:Name="{target_field}"' in native_xaml
    assert 'TextChanged="AdbPortBox_TextChanged"' in native_xaml
    assert 'Click="RevertAdbPortDraft_Click"' in native_xaml
    assert "_adbPortDraftDirty" in native_code
    assert "if (!_adbPortDraftDirty && _configuredAdbPort is not null)" in native_code
    assert "Draft retained locally" in native_code
    assert "!AdbPortBox.IsKeyboardFocusWithin" not in native_code
    assert 'new { action = "set_adb_port", adb_port = adbPort }' in native_code


def test_native_overview_uses_contextual_exact_action_slots_and_compact_status():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert 'Header="Timed pause…"' in native_xaml
    assert 'x:Name="ManualSurrenderPanel" Visibility="Collapsed"' in native_xaml
    assert 'x:Name="StartBattleButton"' in native_xaml
    assert 'x:Name="AttachBattleButton"' in native_xaml
    assert "StartBattleButton.Visibility = start.Available" in native_code
    assert "AttachBattleButton.Visibility = attach.Available" in native_code
    assert "ManualSurrenderPanel.Visibility = showTakeManualControl" in native_code
    assert (
        "var showReturnControl = giveBack.Available || manualOngoing;"
        in native_code
    )

    for field in (
        "CurrentStrategyValueText",
        "NextStrategyLabelText",
        "NextStrategyValueText",
        "SelectedStrategyValueText",
        "TerminalPolicyText",
        "LatestBattleCompactText",
    ):
        assert f'x:Name="{field}"' in native_xaml
    assert 'x:Name="StrategyActionHelpText"' in native_xaml
    assert (
        "StrategyActionHelpText.Visibility = _strategySelection.Dirty"
        in native_code
    )
    assert (
        "TournamentValidationText.Visibility = validationRelevant"
        in native_code
    )
    assert (
        "CaptureSetupText.Visibility = model?.SetupCapture is not null"
        in native_code
    )
    assert "ConfigureRunText.Visibility = configuredSkips.Count > 0" in native_code
    assert "Saved request:" in native_code
    assert "awaiting acknowledgement" in native_code
    assert 'new { action = tag }' in native_code


def test_native_strategy_selection_auto_queues_and_retains_failed_intent():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(encoding="utf-8")
    native_code = (native_root / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    coordinator = (native_root / "StrategySelectionCoordinator.cs").read_text(
        encoding="utf-8"
    )
    profiles = (native_root / "StrategyProfilesWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    history = (native_root / "StrategyHistoryWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    capture = (native_root / "SetupCaptureWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert 'Content="Use next battle"' not in native_xaml
    assert 'Content="Save startup default"' in native_xaml
    assert 'Content="Switch this battle"' in native_xaml
    assert '"Retry next battle"' in native_code
    assert "private bool _strategyDegradedObserver;" in native_code
    assert "&& !_strategyDegradedObserver" in native_code
    assert "This attached battle must remain a degraded observer" in native_code
    assert "QueueStrategyButton.Visibility = !_strategyProcessActive" in native_code
    assert "private async void StrategySelectionBox_SelectionChanged" in native_code
    assert "if (_updatingStrategySelection)" in native_code
    assert "_updatingStrategySelection = true" in native_code
    assert "userDriven: true" in native_code
    assert "await SubmitStrategyRequestAsync(attempt" in native_code
    assert "if (!_strategySelection.Dirty)" in native_code
    assert "if (!userDriven)" in coordinator
    assert "IsNextBoundaryNoOp" in coordinator
    assert "_inFlight?.Token == attempt.Token" in coordinator
    assert "_failedNextBoundaryStrategy = attempt.Strategy" in coordinator
    assert "StrategyRequestOrigin.PublishedRevision" in coordinator
    assert "_handledPublications.Add(notice)" in coordinator
    assert "HasHandledPublication" in coordinator
    assert "_deferredPublication = notice" in coordinator
    assert "response.Request is not { Accepted: true } request" in native_code
    assert "if (!_strategySelection.CompleteFailed(" in native_code
    assert "if (!_strategySelection.CompleteAccepted(" in native_code
    assert "RefreshAfterStrategyResponseAsync" in native_code
    assert 'new { action = "set_strategy", strategy }' in native_code
    assert "apply_to_active_run = true" in native_code
    assert "_strategySelection.Dirty && selected is not null" in native_code
    assert "UsePublishedStrategyAsync" in native_code
    assert "_publishedStrategyHandler" in profiles
    assert "_publishedStrategyHandler" in history
    assert "_publishedStrategyHandler" in capture
    publication_handler = native_code.split(
        "private async Task<StrategyPublicationUseResult> UsePublishedStrategyAsync",
        maxsplit=1,
    )[1].split("private void SetStrategyRequestFeedback", maxsplit=1)[0]
    assert publication_handler.index("HasHandledPublication") < (
        publication_handler.index("EnsureStrategyOption")
    )
    start_handler = native_code.split(
        "private async void Process_Click", maxsplit=1
    )[1].split("private void AdbPortBox_TextChanged", maxsplit=1)[0]
    assert 'action = "start"' in start_handler
    assert "strategy," in start_handler


def test_native_status_uses_only_published_dashboard_metrics():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    native_models = (native_root / "Models.cs").read_text(
        encoding="utf-8"
    )
    native_presenter = (native_root / "ActiveRunMetricPresenter.cs").read_text(
        encoding="utf-8"
    )

    assert 'JsonPropertyName("server_time")' in native_models
    assert 'JsonPropertyName("current_run")' in native_models
    assert 'JsonPropertyName("started_at")' in native_models
    assert 'x:Name="RunElapsedMetricPanel"' in native_xaml
    assert 'x:Name="RunElapsedText"' in native_xaml
    assert 'x:Name="WaveMetricPanel" Visibility="Collapsed"' in native_xaml
    assert (
        'x:Name="CoinsMinuteMetricPanel" Visibility="Collapsed"'
        in native_xaml
    )
    assert "FormatRunElapsed(" in native_code
    assert "status.ServerTime" in native_code
    assert "RunElapsedMetricPanel.Visibility = processActive" in native_code
    assert 'GameState: "active_battle"' in native_code
    assert 'JsonPropertyName("active_run_metrics")' in native_models
    assert 'JsonPropertyName("whole_run")' in native_models
    assert 'JsonPropertyName("interval")' in native_models
    metric_columns = {
        "WholeRunCphMetricPanel": 0,
        "IntervalCphMetricPanel": 1,
        "CellsHourMetricPanel": 2,
        "WavesHourMetricPanel": 3,
        "EffectiveSpeedMetricPanel": 4,
        "MetricCheckpointPanel": 5,
    }
    for field, column in metric_columns.items():
        assert (
            f'<StackPanel Grid.Row="2" Grid.Column="{column}"\n'
            f'                      x:Name="{field}"'
        ) in native_xaml
    assert '<UniformGrid Grid.Row="2" Grid.ColumnSpan="6"' not in native_xaml
    assert 'Text="RECENT CPH"' in native_xaml
    assert "Whole-run CPH remains the battle average." in native_xaml
    assert 'ToolTip="Speed OCR from periodic Running frames.' in native_xaml
    assert 'speedOcrExpected ? "OCR missed" : "—";' in native_code
    assert "ActiveRunMetricPresenter.Present(" in native_code
    assert "RenderActiveRunMetrics(" in native_code
    assert native_code.count("ClearActiveRunMetrics();") >= 3
    assert "observedRoundIdentity" in native_code
    assert "metrics.ActiveRoundIdentityFingerprint" in native_presenter
    assert "metrics.AgeSeconds" in native_presenter
    assert "metrics.CheckpointWave" in native_presenter
    assert "metrics.SaveRevision" in native_presenter
    assert 'status is "observed" or "partial"' in native_presenter

    for unsupported_field in (
        "ExpectedRunDurationText",
        "PeakCoinsMinuteText",
        "RecoveryCountdownText",
        "ReturnNowButton",
        "ExtendRecoveryButton",
        "CancelRecoveryButton",
    ):
        assert unsupported_field not in native_xaml
        assert unsupported_field not in native_code


def test_better_control_clients_expose_distinct_workflows_and_capture_review():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    capture_xaml = (native_root / "SetupCaptureWindow.xaml").read_text(
        encoding="utf-8"
    )
    authoring_xaml = (native_root / "StrategyProfilesWindow.xaml").read_text(
        encoding="utf-8"
    )
    authoring_code = (native_root / "StrategyProfilesWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    native_compatibility = (
        native_root / "ControlSurfaceCompatibility.cs"
    ).read_text(encoding="utf-8")
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    native_models = (native_root / "Models.cs").read_text(
        encoding="utf-8"
    )

    assert 'id="startBattleButton"' in html
    assert 'id="attachBattleButton"' in html
    assert 'id="takeManualControlButton"' in html
    assert 'id="returnControlButton"' in html
    assert 'id="manualControlDialog"' in html
    assert 'id="captureSetupButton"' in html
    assert 'id="captureSetupDialog"' in html
    assert 'id="captureEvidenceSource"' in html
    assert '["startBattleButton", "start_battle"]' in script
    assert '["attachBattleButton", "attach_battle"]' in script
    assert '["returnControlButton", "return_control"]' in script
    assert 'manual_surrender_collection' in script
    assert 'expected_review_fingerprint' in script
    assert 'retained_return_control_refresh' in script
    assert 'Content="Start Battle"' in native_xaml
    assert 'Content="Attach to Battle"' in native_xaml
    assert 'Content="Take Manual Control"' in native_xaml
    assert 'Content="Return Control"' in native_xaml
    assert 'Content="Capture current setup as…"' in native_xaml
    assert 'Content="Review differences"' in capture_xaml
    assert 'x:Name="CapturedDraftsList"' in authoring_xaml
    assert "GetCapturedStrategyDraftAsync" in authoring_code
    assert '"attached_automation_restart"' not in native_compatibility
    assert '"observed_game_speed"' in native_compatibility
    assert 'id="observedSpeed"' in html
    assert 'id="gameSpeedObserved"' in html
    assert 'Content="Start Automation"' in native_xaml
    assert 'Content="Start Battle"' in native_xaml
    assert 'Content="Attach to Battle"' in native_xaml
    assert 'Content="Take Manual Control"' in native_xaml
    assert 'Content="Return Control"' in native_xaml
    assert 'data-control-action="start_battle"' in html
    assert 'data-control-action="attach_battle"' in html
    assert 'data-control-action="take_manual_control"' in html
    assert 'data-control-action="return_control"' in html
    assert "availability.available !== true" in script
    assert "BETTER_CONTROL_MINIMUM_REVISION = 30" in script
    assert "(action === \"start\" && !betterControlCompatible)" in script
    assert '"terminalPolicyStatus"' in script
    assert "workflow?.status" in script
    assert "actions.enable?.available !== true" in script
    assert 'model.Actions.TryGetValue(name, out var availability)' in native_code
    assert "workflow.Status" in native_code
    assert "enable.Available" in native_code
    assert "_serverCompatibility?.IsCompatible != true" in native_code
    assert "PauseButton.IsEnabled = pause.Available" in native_code
    assert "terminalPolicyStatus.Status" in native_code
    assert 'JsonPropertyName("status")' in native_models
    assert 'JsonPropertyName("reason")' in native_models
    assert 'JsonPropertyName("acquisition_source")' in native_models
    assert "startup_gate_policy" not in script
    assert "run_state" not in script
    assert "restart_attached" not in script
    assert 'Content="Use next battle"' not in native_xaml
    assert 'Content="Save startup default"' in native_xaml
    assert '"Retry next battle"' in native_code
    assert 'Content="Switch this battle"' in native_xaml
    assert 'x:Name="StrategyProfilesButton"' in native_xaml
    assert 'Header="Strategy profiles…"' in native_xaml
    assert '"strategy_authoring_v1"' in native_compatibility
    assert '"strategy_authoring_profile_lifecycle_v1"' in native_compatibility
    assert '"strategy_authoring_specialized_editors_v1"' in native_compatibility
    assert '"strategy_profile_catalog_v1"' in native_compatibility
    assert '"strategy_profile_editor_v2"' in native_compatibility
    profile_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    profile_xaml = (profile_root / "StrategyProfilesWindow.xaml").read_text(
        encoding="utf-8"
    )
    profile_code = (profile_root / "StrategyProfilesWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    assert 'Text="BASES"' in profile_xaml
    assert 'Text="STRATEGIES"' in profile_xaml
    assert 'Content="Show active only"' in profile_xaml
    assert 'Content="Show all settings"' in profile_xaml
    assert 'Content="Reset to inherited"' in profile_xaml
    assert 'Content="Review &amp; Publish..."' in profile_xaml
    assert 'Content="Rename Strategy"' in profile_xaml
    assert 'Content="Delete Strategy..."' in profile_xaml
    assert 'operation = "preview_rebase"' in profile_code
    assert 'operation = "retire_strategy"' in profile_code
    assert 'reviewed_rebase_fingerprint' in profile_code
    assert 'Text="HOST HEALTH"' in native_xaml
    assert 'Text="WINDOWS HOST"' in native_xaml
    assert 'Text="BLUESTACKS"' in native_xaml
    assert 'x:Name="BlueStacksCpuText"' in native_xaml
    assert 'Text="OTHER WINDOWS LOAD"' in native_xaml
    assert 'Text="OBSERVED SPEED"' in native_xaml


def test_browser_client_executes_capture_and_workflow_transition_model():
    model_path = STATIC_DIR / "client_model.js"
    script = f"""
const assert = require('assert');
const model = require({json.dumps(str(model_path))});
const requested = {{request_id: 'capture-1', status: 'requested', updated_at: '2026-08-07T10:00:00Z'}};
const acknowledged = {{request_id: 'capture-1', status: 'acknowledged', updated_at: '2026-08-07T10:00:01Z'}};
const ready = {{request_id: 'capture-1', status: 'ready', updated_at: '2026-08-07T10:00:02Z', preview_fingerprint: 'a'.repeat(64)}};
const saved = {{request_id: 'capture-1', status: 'saved', updated_at: '2026-08-07T10:00:03Z', preview_fingerprint: 'a'.repeat(64)}};
assert.strictEqual(model.chooseLatestCapture(acknowledged, requested), acknowledged);
assert.strictEqual(model.chooseLatestCapture(ready, requested), ready);
assert.strictEqual(model.chooseLatestCapture(ready, saved), saved);
assert.strictEqual(model.captureCatalogMatches(ready, {{...ready}}), true);
assert.strictEqual(model.captureCatalogMatches(ready, requested), false);
assert.strictEqual(model.setupCaptureOpenAction(null, {{available: true}}), 'request');
assert.strictEqual(model.setupCaptureOpenAction(requested, {{available: false}}), 'progress');
assert.strictEqual(model.setupCaptureOpenAction(ready, {{available: false}}), 'review');
for (const status of ['saved', 'cancelled', 'unavailable', 'interrupted', 'failed']) {{
  const terminal = {{request_id: 'capture-1', status}};
  assert.strictEqual(model.captureIsTerminal(terminal), true);
  assert.strictEqual(model.setupCaptureOpenAction(terminal, {{available: true}}), 'inspect');
}}
for (const status of ['requested', 'pending', 'acknowledged']) {{
  assert.strictEqual(model.workflowPresentation(status).pending, true);
}}
for (const status of ['no_op', 'stale', 'rejected', 'unavailable', 'interrupted']) {{
  assert.strictEqual(model.workflowPresentation(status).terminal, true);
}}
assert.strictEqual(model.workflowPresentation('rejected').label, 'Rejected');
const activeMapping = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{
    state: 'active_local',
    mapping_id: 'data-9-game-1101',
    raw_value: 41,
    semantic_value: 'Being Annihilator',
    scope: {{slot_key: 'cannon_assist'}},
    reason: 'canonical integration is pending',
  }}],
}});
assert.strictEqual(activeMapping.visible, true);
assert.strictEqual(activeMapping.severity, 'warning');
assert.match(activeMapping.title, /Module identity/);
assert.match(activeMapping.detail, /cannon_assist/);
assert.strictEqual(model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{state: 'integrated'}}],
}}).visible, false);
assert.strictEqual(model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{state: 'canonical_conflict'}}],
}}).severity, 'danger');
assert.strictEqual(model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: false,
  items: [],
  reason: 'malformed local confirmation',
}}).severity, 'danger');
const mixedMapping = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [
    {{state: 'active_local', reason: 'pending'}},
    {{state: 'canonical_conflict', reason: 'conflicting canonical value'}},
  ],
}});
assert.strictEqual(mixedMapping.severity, 'danger');
assert.match(mixedMapping.detail, /conflicting canonical value/);
assert.strictEqual(
  model.confirmedLocalMappingPresentation(undefined).visible,
  true,
);
assert.strictEqual(model.confirmedLocalMappingPresentation({{
  schema_version: 1,
  available: true,
  items: [],
}}).severity, 'danger');
const mappingReview = {{
  schema_version: 3,
  capability: 'save_mapping_staged_candidate_v1',
  operation: 'review',
  candidate_record_id: 'a'.repeat(64),
  reviewed_proposal_fingerprint: 'c'.repeat(64),
  reviewed_base_commit: '1'.repeat(40),
  canonical_mapping_fingerprint: 'f'.repeat(64),
  repository: {{
    main_commit: '1'.repeat(40),
    staging_ref: 'refs/thetower/save-mapping-candidate',
    staged_commit: null,
    production_clean: true,
    integration_available: true,
    code: '',
  }},
  proposal: {{
    schema_version: 2,
    record_id: 'a'.repeat(64),
    targets: [{{
      path: 'config/player_save_versions/data.json',
      mapping_id: 'data-9-game-1101',
      expected_sha256: 'd'.repeat(64),
      operations: [{{op: 'add', path: '/values/-', value: 7}}],
    }}],
  }},
  rendered_targets: [{{
    path: 'config/player_save_versions/data.json',
    mapping_id: 'data-9-game-1101',
    before_sha256: 'd'.repeat(64),
    after_sha256: 'e'.repeat(64),
    changed: true,
    mode: 436,
  }}],
  stage: {{available: true, code: '', reason: ''}},
}};
assert.strictEqual(
  model.saveMappingReviewIsCurrent(
    mappingReview,
    'a'.repeat(64),
  ),
  true,
);
assert.strictEqual(
  model.saveMappingIntegrateAvailability(
    mappingReview,
    'a'.repeat(64),
  ).available,
  true,
);
const recoveryReview = {{
  ...mappingReview,
  recovery_required: true,
  repository: {{
    ...mappingReview.repository,
    main_commit: 'b'.repeat(40),
    staged_commit: 'b'.repeat(40),
    production_clean: false,
    integration_available: false,
    code: 'transaction_recovery_required',
  }},
  proposal: {{
    ...mappingReview.proposal,
    targets: mappingReview.proposal.targets.map((target) => ({{
      ...target,
      operations: [],
    }})),
  }},
  stage: {{
    available: true,
    code: 'transaction_recovery_required',
    reason: 'retry exact durable transaction once',
  }},
}};
assert.strictEqual(
  model.saveMappingIntegrateAvailability(
    recoveryReview,
    'a'.repeat(64),
  ).available,
  true,
);
assert.strictEqual(
  model.saveMappingIntegrateAvailability(
    mappingReview,
    'changed',
  ).code,
  'review_stale',
);
assert.strictEqual(model.saveMappingIntegrationCompatible({{
  api_version: 1,
  server_revision: 45,
  capabilities: [
    'save_mapping_staged_candidate_v1',
    'save_mapping_candidate_disposition_v1',
    'save_mapping_automatic_promotion_v1',
    'save_mapping_machine_verification_v1',
  ],
}}), true);
assert.strictEqual(model.saveMappingIntegrationCompatible({{
  api_version: 1,
  server_revision: 44,
  capabilities: [
    'save_mapping_staged_candidate_v1',
    'save_mapping_candidate_disposition_v1',
    'save_mapping_automatic_promotion_v1',
    'save_mapping_machine_verification_v1',
  ],
}}), false);
assert.strictEqual(model.saveMappingIntegrationCompatible({{
  api_version: 1,
  server_revision: 45,
  capabilities: [
    'save_mapping_staged_candidate_v1',
    'save_mapping_candidate_disposition_v1',
    'save_mapping_automatic_promotion_v1',
  ],
}}), false);
const dismissedResult = {{
  schema_version: 1,
  capability: 'save_mapping_candidate_disposition_v1',
  operation: 'dismiss',
  disposition: 'dismissed',
  candidate_record_id: 'a'.repeat(64),
  event_id: 'd'.repeat(64),
  recorded_at: '2026-08-15T12:00:00+00:00',
  changed: true,
  evidence_preserved: true,
}};
assert.strictEqual(model.saveMappingDismissedResultValidation(
  dismissedResult,
  'a'.repeat(64),
).valid, true);
assert.strictEqual(model.saveMappingDismissedResultValidation(
  {{...dismissedResult, evidence_preserved: false}},
  'a'.repeat(64),
).valid, false);
assert.strictEqual(model.saveMappingDismissedResultValidation(
  dismissedResult,
  'b'.repeat(64),
).valid, false);
const integratedResult = {{
  schema_version: 3,
  capability: 'save_mapping_staged_candidate_v1',
  operation: 'integrate',
  disposition: 'promoted',
  idempotent: false,
  candidate_record_id: 'a'.repeat(64),
  reviewed_proposal_fingerprint: 'c'.repeat(64),
  base_commit: '1'.repeat(40),
  staging_ref: 'refs/thetower/save-mapping-candidate',
  staged_commit: 'b'.repeat(40),
  committed: true,
  staged: true,
  promoted: true,
  published: true,
  automatic_retry: false,
  agent_required: false,
  code: '',
  reason: '',
  next_action: 'Await a fresh stable decode.',
  agent_review_prompt: '',
  mapping_invariants: 'passed',
  promotion_validation: 'pending',
  targets: [{{
    path: 'config/player_save_versions/data.json',
    mapping_id: 'data-9-game-1101',
    before_sha256: 'd'.repeat(64),
    after_sha256: 'e'.repeat(64),
    changed: true,
    mode: 436,
  }}],
}};
assert.strictEqual(model.saveMappingIntegratedResultValidation(
  integratedResult,
  mappingReview,
).valid, true);
assert.match(
  model.saveMappingIntegratedPresentation(
    integratedResult,
    mappingReview,
  ).detail,
  /Production and origin contain it/,
);
assert.strictEqual(model.saveMappingIntegratedResultValidation(
  {{...integratedResult, promoted: false}},
  mappingReview,
).valid, false);
const queuedResult = {{
  ...integratedResult,
  disposition: 'promotion_queued',
  promoted: false,
  published: false,
  automatic_retry: true,
  agent_required: false,
  code: 'promotion_owner_busy',
  reason: 'Another promotion owns the transaction.',
  agent_review_prompt: 'Ask an agent if this persists.',
}};
assert.strictEqual(model.saveMappingIntegratedResultValidation(
  queuedResult,
  mappingReview,
).valid, true);
const cleanupQueuedResult = {{
  ...queuedResult,
  promoted: true,
  published: true,
  code: 'promotion_owner_release_failed',
  reason: 'Exact promotion owner still needs release.',
}};
assert.strictEqual(model.saveMappingIntegratedResultValidation(
  cleanupQueuedResult,
  mappingReview,
).valid, true);
assert.match(
  model.saveMappingIntegratedPresentation(
    cleanupQueuedResult,
    mappingReview,
  ).title,
  /cleanup queued/,
);
for (const invalid of [
  {{...integratedResult, candidate_record_id: 'f'.repeat(64)}},
  {{...integratedResult, committed: false}},
  {{...integratedResult, promotion_validation: 'passed'}},
  {{...integratedResult, targets: [{{...integratedResult.targets[0], after_sha256: 'bad'}}]}},
  {{...integratedResult, targets: [integratedResult.targets[0], integratedResult.targets[0]]}},
]) {{
  assert.strictEqual(model.saveMappingIntegratedResultValidation(
    invalid,
    mappingReview,
  ).valid, false);
}}
assert.strictEqual(model.saveMappingFailurePresentation({{
  code: 'staging_ref_occupied', message: 'pending candidate',
}}).uncertain, false);
assert.strictEqual(model.saveMappingFailurePresentation({{
  code: 'commit_state_uncertain', message: 'inspect',
}}).uncertain, true);
const unchanged = model.saveMappingFailurePresentation({{
  code: 'staging_ref_update_failed', message: 'Private ref stayed empty.',
}});
assert.strictEqual(unchanged.uncertain, false);
assert.match(unchanged.detail, /retry once only when directed/);
const promotion = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{state: 'promotion_pending', reason: 'awaiting production'}}],
}});
assert.strictEqual(promotion.severity, 'info');
assert.match(promotion.title, /automatic promotion/);
const restaging = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{state: 'restaging_required', reason: 'main advanced'}}],
}});
assert.strictEqual(restaging.severity, 'warning');
assert.match(restaging.title, /restaged/);
const promotionDominatesQueue = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [
    {{state: 'active_local', reason: 'ordinary local queue'}},
    {{state: 'promotion_pending', reason: 'awaiting exact production promotion'}},
  ],
}});
assert.match(promotionDominatesQueue.title, /automatic promotion/);
assert.match(promotionDominatesQueue.detail, /awaiting exact production promotion/);
const cleanup = model.confirmedLocalMappingPresentation({{
  schema_version: 2,
  available: true,
  items: [{{state: 'promotion_cleanup_pending', reason: 'owner release pending'}}],
}});
assert.match(cleanup.title, /automatic cleanup/);
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    browser = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'openAction !== "request"' in browser
    assert "async function retrySetupCapture()" in browser
    assert 'id="retryCaptureButton"' in html
    assert 'id="reviewSaveMappingsButton"' in html
    assert 'id="saveMappingIntegrationDialog"' in html
    assert 'id="saveMappingWorkspaceSelect"' not in html
    assert "reviewed_proposal_fingerprint" in browser
    assert "Integrate reviewed mapping" in html
    assert "reviewButton.hidden = Boolean(item && item.review_available !== true)" in browser
    assert "dismissButton.hidden = Boolean(item && item.dismiss_available !== true)" in browser
    assert "integrateButton.hidden = Boolean(item && item.review_available !== true)" in browser
    assert "Exact terminal Game Over/save proofs need no operator review" in html
    assert "private staging ref" in browser
    assert "saveMappingIntegratedResultValidation" in browser
    assert "saveMappingDismissedResultValidation" in browser
    assert "saveMappingSelectionStillCurrent" in browser
    assert "Interrupted integration requires recovery" in browser
    assert 'id="copySaveMappingAgentPromptButton"' in html
    assert 'id="dismissSaveMappingObservationButton"' in html
    assert 'operation: "dismiss"' in browser
    assert "The original durable receipt will be preserved" in browser
    assert "Agent-review request" in browser
    assert 'byId("saveMappingCandidateSelect").disabled = busy' in browser
    assert "saveMappingWorkspaceSelect" not in browser
    assert "state.saveMappingResult != null" in browser
    assert 'addEventListener("cancel"' in browser


def test_native_incompatible_api_has_prominent_start_mitigation():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert 'x:Name="CompatibilityBanner"' in native_xaml
    assert (
        'Text="LINUX API UPDATE REQUIRED — AUTOMATION START IS DISABLED"'
        in native_xaml
    )
    assert 'x:Name="RestartControlSurfaceBannerButton"' in native_xaml
    assert native_xaml.count('ToolTipService.ShowOnDisabled="True"') >= 3
    assert "Connected Linux API is incompatible" in native_code
    assert "wait for this banner to disappear" in native_code
    assert "it does not start automation or alter the game" in native_code
    assert "StartBlockerDescription" in native_code


def test_per_user_tunnel_host_owns_independent_api_and_adb_ssh_processes():
    windows_root = Path(__file__).parents[1] / "windows"
    gui_root = windows_root / "TheTower.ControlSurface"
    host_root = windows_root / "TheTower.TunnelHost"
    core_root = windows_root / "TheTower.TunnelHost.Core"
    protocol_root = windows_root / "TheTower.TunnelProtocol"
    process_code = (host_root / "OpenSshTunnelProcess.cs").read_text(
        encoding="utf-8"
    )
    protocol = (protocol_root / "TunnelHostProtocol.cs").read_text(
        encoding="utf-8"
    )
    identity = (protocol_root / "UserScopedIpcIdentity.cs").read_text(
        encoding="utf-8"
    )
    supervisor = (core_root / "TunnelSupervisor.cs").read_text(
        encoding="utf-8"
    )
    program = (host_root / "Program.cs").read_text(encoding="utf-8")
    pipe_server = (host_root / "TunnelHostNamedPipeServer.cs").read_text(
        encoding="utf-8"
    )
    job = (core_root / "WindowsKillOnCloseJob.cs").read_text(
        encoding="utf-8"
    )
    connection = (gui_root / "TunnelHostConnection.cs").read_text(
        encoding="utf-8"
    )
    window_code = (gui_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert not (gui_root / "SshTunnelManager.cs").exists()
    assert '"-L"' in protocol
    assert '"-R"' in protocol
    assert (
        '$"127.0.0.1:{configuration.LinuxAdbPort}:127.0.0.1:'
        '{configuration.WindowsBlueStacksAdbPort}"'
        in protocol
    )
    assert '"BatchMode=yes"' in process_code
    assert '"StrictHostKeyChecking=yes"' in process_code
    assert '"ConnectTimeout=10"' in process_code
    assert '"ExitOnForwardFailure=yes"' in process_code
    assert '"ServerAliveInterval=30"' in process_code
    assert '"ServerAliveCountMax=3"' in process_code
    assert "PipeOptions.CurrentUserOnly" in identity
    assert "WindowsIdentity.GetCurrent().User" in identity
    assert "private readonly TunnelKind _kind" in supervisor
    assert program.count("new TunnelSupervisor") == 2
    assert "TunnelKind.Api" in program
    assert "TunnelKind.Adb" in program
    assert "AssignCurrentProcess" in program
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in job
    assert "KillOnJobCloseLimit = 0x00002000" in job
    assert "TunnelHostConnection _tunnelHost" in window_code
    assert 'FileName = "ssh.exe"' not in window_code
    assert "ShutdownHost" not in window_code
    assert "await _tunnelHost.DisposeAsync()" in window_code
    assert "RetrySummary" in window_code
    assert "The API tunnel is unchanged" in window_code
    assert 'ErrorCode = "protocol_mismatch"' in pipe_server
    assert "MinimumProtocolVersion" in pipe_server
    assert "MaximumProtocolVersion" in pipe_server
    assert "HostExecutablePath" in pipe_server
    assert "StopVerifiedIncompatibleHostAsync" in connection
    assert "compatibility.HostProcessId" in connection
    assert "compatibility.HostStartedAt" in connection
    assert "ConfirmShutdown = true" in connection


def test_native_control_surface_exposes_independent_api_service_and_tunnel_health():
    windows_root = Path(__file__).parents[1] / "windows"
    native_root = windows_root / "TheTower.ControlSurface"
    service_controller = (
        windows_root
        / "TheTower.TunnelHost"
        / "OpenSshLinuxApiServiceController.cs"
    ).read_text(
        encoding="utf-8"
    )
    host_protocol = (
        windows_root / "TheTower.TunnelProtocol" / "TunnelHostProtocol.cs"
    ).read_text(encoding="utf-8")
    window = (native_root / "MainWindow.xaml").read_text(encoding="utf-8")
    window_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert 'x:Name="LinuxApiServiceStatusText"' in window
    assert 'x:Name="ConnectionText"' in window
    assert 'x:Name="ApiTunnelTopStatusText"' in window
    assert 'x:Name="AdbTunnelTopStatusText"' in window
    assert 'x:Name="ToggleControlSurfaceServiceButton"' in window
    assert 'Content="Restart API service"' in window
    assert 'Header="Restart API tunnel"' in window
    assert 'Header="Restart ADB tunnel"' in window
    assert 'x:Name="TunnelHostStatusText"' in window
    assert 'Content="Restart tunnel host..."' in window
    assert 'Text="AUTOMATION SERVICE"' in window
    assert "LinuxApiServiceAction.Start" in service_controller
    assert "LinuxApiServiceAction.Stop" in service_controller
    assert "LinuxApiServiceAction.Restart" in service_controller
    assert '"systemctl", "--user", verb, ControlSurfaceService' in service_controller
    assert '"--property=ActiveState"' in service_controller
    assert "params string[] commandArguments" in service_controller
    assert "ControlSurfaceService" in service_controller
    assert "RemoteCommand" not in host_protocol
    assert "ServiceUnit" not in host_protocol
    assert "RefreshControlSurfaceServiceStatusAsync" in window_code
    assert "RestartApiTunnel_Click" in window_code
    assert "RestartAdbTunnel_Click" in window_code
    assert "Unavailable — service stopped" in window_code


def test_windows_publish_package_requires_gui_and_tunnel_host_executables():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    powershell = (native_root / "publish.ps1").read_text(encoding="utf-8")
    linux = (native_root / "publish-linux.sh").read_text(encoding="utf-8")

    for script in (powershell, linux):
        assert "TheTower.ControlSurface.exe" in script
        assert "TheTower.TunnelHost.exe" in script
        assert "TheTower.TunnelHost.csproj" in script
        assert "previous" in script
        assert "prior package 1" in script.lower()
        assert ".publish.lock" in script

    assert "Assert-CompletePackage" in powershell
    assert ".Length -le 0" in powershell
    assert "validate_package" in linux
    assert "! -s" in linux


def test_linux_windows_publisher_retains_two_complete_prior_packages(tmp_path):
    source_root = Path(__file__).parents[1]
    source_native = source_root / "windows" / "TheTower.ControlSurface"
    windows_root = tmp_path / "windows"
    native_root = windows_root / "TheTower.ControlSurface"
    host_root = windows_root / "TheTower.TunnelHost"
    native_root.mkdir(parents=True)
    host_root.mkdir(parents=True)
    (native_root / "TheTower.ControlSurface.csproj").write_text(
        "<Project />\n",
        encoding="utf-8",
    )
    (host_root / "TheTower.TunnelHost.csproj").write_text(
        "<Project />\n",
        encoding="utf-8",
    )
    publisher = native_root / "publish-linux.sh"
    shutil.copy2(source_native / "publish-linux.sh", publisher)
    publisher.chmod(0o755)

    sdk_base = tmp_path / "fake-sdk" / "8.0.423"
    targets = (
        sdk_base
        / "Sdks"
        / "Microsoft.NET.Sdk.WindowsDesktop"
        / "targets"
        / "Microsoft.NET.Sdk.WindowsDesktop.targets"
    )
    targets.parent.mkdir(parents=True)
    targets.write_text("<Project />\n", encoding="utf-8")
    fake_dotnet = tmp_path / "fake-dotnet"
    fake_dotnet.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "--info" ]]; then
    printf ' Base Path: {sdk_base}\\n'
    exit 0
fi
if [[ "${{1:-}}" != "publish" ]]; then
    exit 2
fi
project="${{2:-}}"
shift 2
output=""
while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "--output" ]]; then
        output="${{2:-}}"
        break
    fi
    shift
done
if [[ -z "$output" ]]; then
    exit 3
fi
mkdir -p -- "$output"
if [[ "$project" == *"TheTower.TunnelHost.csproj" ]]; then
    printf '%s' "${{FAKE_VERSION}}:host" > "$output/TheTower.TunnelHost.exe"
else
    if [[ "${{FAKE_FAIL_GUI:-0}}" == "1" ]]; then
        exit 9
    fi
    printf '%s' "${{FAKE_VERSION}}:gui" > "$output/TheTower.ControlSurface.exe"
fi
""",
        encoding="utf-8",
    )
    fake_dotnet.chmod(0o755)

    def publish(
        version: str,
        *,
        fail_gui: bool = False,
    ) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["THETOWER_DOTNET"] = str(fake_dotnet)
        environment["FAKE_VERSION"] = version
        environment["FAKE_FAIL_GUI"] = "1" if fail_gui else "0"
        return subprocess.run(
            [str(publisher)],
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_package(path: Path, version: str) -> None:
        assert (path / "TheTower.ControlSurface.exe").read_text(
            encoding="utf-8"
        ) == f"{version}:gui"
        assert (path / "TheTower.TunnelHost.exe").read_text(
            encoding="utf-8"
        ) == f"{version}:host"

    publish_root = native_root / "publish"
    for version in ("one", "two", "three", "four"):
        result = publish(version)
        assert result.returncode == 0, result.stderr

    assert_package(publish_root / "win-x64", "four")
    assert_package(publish_root / "previous" / "1", "three")
    assert_package(publish_root / "previous" / "2", "two")

    failed = publish("five", fail_gui=True)
    assert failed.returncode != 0
    assert_package(publish_root / "win-x64", "four")
    assert_package(publish_root / "previous" / "1", "three")
    assert_package(publish_root / "previous" / "2", "two")
    assert sorted(
        path.name
        for path in publish_root.iterdir()
        if path.name.startswith(".")
    ) == [".publish.lock"]


def test_control_surface_configures_run_from_selected_strategy_checks(tmp_path):
    service = _service(tmp_path)
    initial = service.status()["control"]
    assert initial["startup_gate_waivers"] == {}
    context = initial["startup_gate_context"]
    assert context["strategy"] == "farm_t18"
    assert "bots_preset" in {check["id"] for check in context["checks"]}

    response = service.apply_control(
        {
            "action": "configure_run",
            "skip_checks": ["bots_preset", "auto_pick_perks"],
        }
    )
    staged = response["control"]["startup_gate_waivers"]
    assert set(staged) == {"bots_preset", "auto_pick_perks"}
    assert all(waiver["strategy"] == "farm_t18" for waiver in staged.values())

    defaults = service.apply_control(
        {"action": "configure_run", "skip_checks": []}
    )
    assert defaults["control"]["startup_gate_waivers"] == {}

    service.control_store.set_state("RUNNING", source="test")
    with patch.object(
        service,
        "_runtime_evidence",
        return_value={"active": True, "instances": []},
    ):
        with pytest.raises(ControlSurfaceRequestError, match="Pause automation"):
            service.apply_control(
                {"action": "configure_run", "skip_checks": ["bots_preset"]}
            )

    service.control_store.set_strategy("none", source="test")
    assert service.status()["control"]["startup_gate_context"] == {
        "strategy": "none",
        "checks": [],
    }
    with pytest.raises(ControlSurfaceRequestError, match="not configurable"):
        service.apply_control(
            {"action": "configure_run", "skip_checks": ["bots_preset"]}
        )


def test_activity_filters_levels_before_applying_limit(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[DEBUG 2026-07-19 17:00:00] first debug\n"
        "[WARN 2026-07-19 17:00:01] first warning\n"
        "[INFO 2026-07-19 17:00:02] information\n"
        "[ERROR 2026-07-19 17:00:03] first error\n"
        "[WARN 2026-07-19 17:00:04] latest warning\n",
        encoding="utf-8",
    )

    response = _service(tmp_path).activity(
        limit=2,
        levels=["warn", "ERROR"],
    )

    assert response["available_levels"] == ["DEBUG", "ERROR", "INFO", "WARN"]
    assert [(entry["level"], entry["message"]) for entry in response["items"]] == [
        ("ERROR", "first error"),
        ("WARN", "latest warning"),
    ]


def test_activity_preserves_result_and_input_levels(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[ACTION 2026-07-19 17:00:00] Reviewing mission rewards\n"
        "[INPUT 2026-07-19 17:00:01] Open Daily Missions\n"
        "[RESULT 2026-07-19 17:00:02] Mission reward review complete\n",
        encoding="utf-8",
    )

    response = _service(tmp_path).activity(
        levels=["input", "RESULT"],
    )

    assert response["available_levels"] == ["ACTION", "INPUT", "RESULT"]
    assert [(entry["level"], entry["message"]) for entry in response["items"]] == [
        ("INPUT", "Open Daily Missions"),
        ("RESULT", "Mission reward review complete"),
    ]


def test_operational_activity_folds_only_correlated_completed_pairs(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[ACTION 2026-07-19 17:00:00] Recording the perk selection timeline\n"
        "[DEBUG 2026-07-19 17:00:00] [PERK_TIMELINE] mode=full "
        "[OPERATION] id=perk-100\n"
        "[WARN 2026-07-19 17:00:01] A separate warning remains visible\n"
        "[RESULT 2026-07-19 17:00:02] Perk timeline selection recorded — "
        "Bounce Shot +2\n"
        "[DEBUG 2026-07-19 17:00:02] [PERK_TIMELINE] result=recorded "
        "[OPERATION] id=perk-100\n"
        "[ACTION 2026-07-19 17:00:03] A still-running operation\n"
        "[DEBUG 2026-07-19 17:00:03] [PENDING] state=running "
        "[OPERATION] id=pending-1\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)

    operational = service.activity(
        levels=["ACTION", "RESULT", "WARN", "ERROR", "FAIL"],
    )
    all_levels = service.activity()
    actions_only = service.activity(levels=["ACTION"])
    results_only = service.activity(levels=["RESULT"])

    assert [
        (entry["level"], entry["message"])
        for entry in operational["items"]
    ] == [
        ("WARN", "A separate warning remains visible"),
        ("RESULT", "Perk timeline selection recorded — Bounce Shot +2"),
        ("ACTION", "A still-running operation"),
    ]
    folded_result = operational["items"][1]
    assert folded_result["operation_id"] == "perk-100"
    assert folded_result["collapsed"] is True
    assert (
        folded_result["collapsed_action"]
        == "Recording the perk selection timeline"
    )
    assert [
        entry["level"] for entry in all_levels["items"]
    ] == ["ACTION", "DEBUG", "WARN", "RESULT", "DEBUG", "ACTION", "DEBUG"]
    assert [entry["message"] for entry in actions_only["items"]] == [
        "Recording the perk selection timeline",
        "A still-running operation",
    ]
    assert [entry["message"] for entry in results_only["items"]] == [
        "Perk timeline selection recorded — Bounce Shot +2"
    ]


def test_activity_formats_structured_and_legacy_perk_bundles(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[RESULT 2026-07-30 20:58:44] Perk timeline selections recorded "
        "for wave 950 — x1.98 coins, but tower max health -70.0%, "
        "Perk wave requirement -75.00%\n"
        "[DEBUG 2026-07-30 20:58:44] [PERK_TIMELINE] result=recorded "
        "selection_count=2 close_state=RUNNING "
        '[ACTIVITY_DATA] {"kind":"perk_selection_bundle","items":'
        '[{"alias":"CTO","label":"x1.98 coins, but tower max health '
        '-70.0%"},{"alias":"PWR","label":"Perk wave requirement '
        '-75.00%"}]} [OPERATION] id=perk-950\n'
        "[RESULT 2026-07-30 20:59:44] Perk timeline selections recorded "
        "for wave 1050 — legacy comma-separated list\n"
        "[DEBUG 2026-07-30 20:59:44] [PERK_TIMELINE] result=recorded "
        "selection_count=8 close_state=RUNNING "
        "[OPERATION] id=perk-1050\n",
        encoding="utf-8",
    )

    items = _service(tmp_path).activity(levels=["RESULT"])["items"]

    assert items[0]["display_message"] == (
        "Perk timeline selections recorded for wave 950 — "
        "2 Perks: CTO, PWR"
    )
    assert items[0]["activity_kind"] == "perk_selection_bundle"
    assert items[0]["detail_items"] == [
        {
            "alias": "CTO",
            "label": "x1.98 coins, but tower max health -70.0%",
        },
        {
            "alias": "PWR",
            "label": "Perk wave requirement -75.00%",
        },
    ]
    assert items[1]["display_message"] == (
        "Perk timeline selections recorded for wave 1050 — 8 Perks"
    )
    assert "detail_items" not in items[1]


def test_clients_use_compact_perk_activity_and_preserve_full_copy_text():
    native_root = (
        Path(__file__).parents[1]
        / "windows"
        / "TheTower.ControlSurface"
    )
    native_xaml = (native_root / "MainWindow.xaml").read_text(
        encoding="utf-8"
    )
    native_models = (native_root / "Models.cs").read_text(
        encoding="utf-8"
    )
    native_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )
    browser_script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'Binding="{Binding DisplayMessage}"' in native_xaml
    assert 'Text="{Binding ExpandedMessage}"' in native_xaml
    assert 'JsonPropertyName("detail_items")' in native_models
    assert "• {item.Alias} — {item.Label}" in native_models
    assert "entry.Message" in native_code
    assert "entry.display_message || entry.message" in browser_script


def test_activity_current_run_scope_uses_explicit_boundary(tmp_path, monkeypatch):
    from utils import logger

    log_path = tmp_path / "logs" / "actions.log"
    monkeypatch.setenv("TOWER_ACTION_LOG_PATH", str(log_path))
    logger.log_action("Older run action", console=False)
    scope = logger.start_activity_scope(reason="new_battle_preflight")
    logger.log_action("Current preflight action", console=False)
    service = _service(tmp_path)

    response = service.activity(
        scope="current_run",
        levels=["ACTION"],
    )

    assert scope is not None
    assert response["scope_available"] is True
    assert response["scope_id"] == scope["run_id"]
    assert [entry["message"] for entry in response["items"]] == [
        "Current preflight action"
    ]
    assert service.status()["current_run"] == {
        "run_id": scope["run_id"],
        "started_at": scope["started_at"],
    }


def test_activity_current_run_scope_reports_missing_boundary_fallback(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[ACTION 2026-07-19 17:00:00] Existing activity\n",
        encoding="utf-8",
    )

    response = _service(tmp_path).activity(scope="current_run")

    assert response["scope"] == "current_run"
    assert response["scope_available"] is False
    assert [entry["message"] for entry in response["items"]] == [
        "Existing activity"
    ]


def test_activity_cursor_returns_only_entries_written_after_clear(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[ACTION 2026-07-19 17:00:00] Before clear\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)
    before = service.activity()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("[RESULT 2026-07-19 17:00:01] After clear\n")

    after = service.activity(after=before["end_cursor"])

    assert [entry["message"] for entry in after["items"]] == ["After clear"]


def test_activity_rejects_invalid_scope_and_cursor(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ControlSurfaceRequestError, match="scope"):
        service.activity(scope="battle;drop")
    with pytest.raises(ControlSurfaceRequestError, match="cursor"):
        service.activity(after="not-a-cursor")


def test_activity_audience_filters_preserve_roles_and_complete_order(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    entries = [
        ("INFO", "Runtime lifecycle detail"),
        ("STATUS", "State=RUNNING | Wave=42 | Coins/min=1.2T"),
        ("ACTION", "Reviewing mission rewards — reward badge is visible"),
        ("DEBUG", "[MISSION_REWARDS] badge_source=RUNNING"),
        ("INPUT", "Tap Daily Missions"),
        ("MATCH", "Daily Missions matched at confidence 0.98"),
        ("STATE", "Menu changed to DAILY_MISSIONS"),
        ("RESULT", "Mission reward review complete — claimed 1 reward"),
        ("WARN", "ADB target remains unavailable; retries continue"),
        ("ERROR", "A requested operation could not complete"),
        ("FAIL", "Runtime boundary failed"),
    ]
    log_path.write_text(
        "".join(
            f"[{level} 2026-07-19 17:00:{index:02d}] {message}\n"
            for index, (level, message) in enumerate(entries)
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path)

    operational = service.activity(
        levels=["ACTION", "RESULT", "WARN", "ERROR", "FAIL"],
    )
    diagnostics = service.activity(
        levels=["INPUT", "DEBUG", "MATCH", "STATE"],
    )
    status_only = service.activity(levels=["STATUS"])
    all_levels = service.activity()
    operational_roles = {"ACTION", "RESULT", "WARN", "ERROR", "FAIL"}
    diagnostic_roles = {"INPUT", "DEBUG", "MATCH", "STATE"}

    assert [
        (entry["level"], entry["message"])
        for entry in operational["items"]
    ] == [entry for entry in entries if entry[0] in operational_roles]
    assert [
        (entry["level"], entry["message"])
        for entry in diagnostics["items"]
    ] == [entry for entry in entries if entry[0] in diagnostic_roles]
    assert [
        (entry["level"], entry["message"])
        for entry in status_only["items"]
    ] == [("STATUS", "State=RUNNING | Wave=42 | Coins/min=1.2T")]
    assert [
        (entry["level"], entry["message"])
        for entry in all_levels["items"]
    ] == entries


def test_activity_reports_replacement_log_identity_after_rotation(tmp_path):
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "[INFO 2026-07-19 17:00:00] before rotation\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)

    before = service.activity()
    os.replace(log_path, log_path.with_name("actions.log.1"))
    log_path.write_text(
        "[ACTION 2026-07-19 17:00:01] after rotation\n",
        encoding="utf-8",
    )
    after = service.activity()

    assert before["source_file_id"]
    assert after["source_file_id"]
    assert after["source_file_id"] != before["source_file_id"]
    assert [entry["message"] for entry in after["items"]] == ["after rotation"]


def test_activity_rejects_invalid_level(tmp_path):
    with pytest.raises(ControlSurfaceRequestError):
        _service(tmp_path).activity(levels=["ERROR;DROP"])


def test_http_setup_capture_routes_include_durable_draft_reopen(
    tmp_path,
    monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "captured_setup_draft",
        lambda strategy_id: {
            "schema_version": 1,
            "capability": "save_backed_setup_capture_v2",
            "draft": {
                "id": strategy_id,
                "source": {"kind": "strategy", "id": strategy_id},
            },
        },
    )
    monkeypatch.setattr(
        service,
        "apply_setup_capture",
        lambda payload: {
            "schema_version": 1,
            "request": {
                "accepted": payload == {"operation": "request"},
                "operation": payload.get("operation"),
            },
        },
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
        connection.request("GET", "/api/v1/setup-capture")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["capability"] == "save_backed_setup_capture_v2"

        connection.request(
            "GET",
            "/api/v1/setup-capture/drafts/captured_farm",
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["draft"]["source"]["id"] == "captured_farm"

        body = json.dumps({"operation": "request"})
        connection.request(
            "POST",
            "/api/v1/setup-capture",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["request"] == {
            "accepted": True,
            "operation": "request",
        }
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_save_mapping_integration_routes_catalog_review_and_integrate(
    tmp_path,
    monkeypatch,
):
    service = _service(tmp_path)
    calls = []
    monkeypatch.setattr(
        service,
        "save_mapping_integration",
        lambda: {
            "schema_version": 3,
            "capability": "save_mapping_staged_candidate_v1",
            "available": True,
            "items": [],
        },
    )

    def apply(payload):
        calls.append(payload)
        return {"operation": payload["operation"], "accepted": True}

    monkeypatch.setattr(service, "apply_save_mapping_integration", apply)
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
        connection.request("GET", "/api/v1/save-mapping-integration")
        response = connection.getresponse()
        catalog = json.loads(response.read())
        assert response.status == 200
        assert catalog["capability"] == "save_mapping_staged_candidate_v1"

        for payload in (
            {
                "operation": "review",
                "candidate_record_id": "a" * 64,
            },
            {
                "operation": "dismiss",
                "candidate_record_id": "a" * 64,
            },
            {
                "operation": "stage",
                "candidate_record_id": "a" * 64,
                "reviewed_proposal_fingerprint": "c" * 64,
            },
        ):
            body = json.dumps(payload)
            connection.request(
                "POST",
                "/api/v1/save-mapping-integration",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            result = json.loads(response.read())
            assert response.status == 200
            assert result == {"operation": payload["operation"], "accepted": True}
        assert calls == [
            {
                "operation": "review",
                "candidate_record_id": "a" * 64,
            },
            {
                "operation": "dismiss",
                "candidate_record_id": "a" * 64,
            },
            {
                "operation": "stage",
                "candidate_record_id": "a" * 64,
                "reviewed_proposal_fingerprint": "c" * 64,
            },
        ]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_api_requires_token_but_static_gui_does_not(tmp_path):
    _write_battle(tmp_path)
    server = ControlSurfaceHTTPServer(
        ("127.0.0.1", 0),
        service=_service(tmp_path),
        token="test-secret",
        static_dir=STATIC_DIR,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert b"TheTower Control Surface" in body
        assert "default-src 'self'" in response.getheader("Content-Security-Policy")

        connection.request("GET", "/client_model.js")
        response = connection.getresponse()
        client_model = response.read()
        assert response.status == 200
        assert b"chooseLatestCapture" in client_model

        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        response.read()
        assert response.status == 401

        connection.request(
            "GET",
            "/api/v1/status",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        status_payload = json.loads(response.read())
        assert response.status == 200
        assert "strategy_action_gate" in status_payload
        assert status_payload["strategy_action_gate"]["active"] is False
        assert "emulator_degradation" in status_payload
        assert status_payload["emulator_degradation"]["automatic_ready"] is False

        maintenance_body = json.dumps({"operation": "request"})
        connection.request(
            "POST",
            "/api/v1/host-maintenance",
            body=maintenance_body,
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(maintenance_body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 409
        assert payload["code"] == "emulator_degradation_not_ready"

        operator_body = json.dumps({"operation": "request_operator"})
        connection.request(
            "POST",
            "/api/v1/host-maintenance",
            body=operator_body,
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(operator_body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 409
        assert payload["code"] == "control_not_running"

        connection.request(
            "GET",
            "/api/v1/battles?limit=1",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["items"][0]["wave"] == 520

        connection.request(
            "DELETE",
            "/api/v1/battles/Battle20260719T101126-0700",
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        assert (
            tmp_path
            / "logs"
            / "battles"
            / "Battle20260719T101126-0700.json"
        ).is_file()

        connection.request(
            "DELETE",
            "/api/v1/battles/Battle20260719T101126-0700",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["battle_id"] == "Battle20260719T101126-0700"
        assert (
            tmp_path
            / "logs"
            / "discarded_battles"
            / Path(payload["quarantine_path"]).name
        ).is_dir()

        (tmp_path / "logs" / "actions.log").write_text(
            "[INFO 2026-07-19 17:00:00] information\n"
            "[ERROR 2026-07-19 17:00:01] failure\n",
            encoding="utf-8",
        )
        connection.request(
            "GET",
            "/api/v1/activity?limit=10&levels=ERROR",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert [entry["level"] for entry in payload["items"]] == ["ERROR"]
        activity_cursor = payload["end_cursor"]
        with (tmp_path / "logs" / "actions.log").open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(
                "[RESULT 2026-07-19 17:00:02] after clear cursor\n"
            )
        connection.request(
            "GET",
            "/api/v1/activity?limit=10&levels=RESULT"
            f"&scope=all&after={activity_cursor}",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert [entry["message"] for entry in payload["items"]] == [
            "after clear cursor"
        ]

        connection.request(
            "GET",
            "/api/v1/battles/not-a-battle",
            headers={"Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 404

        body = json.dumps({"action": "enable"})
        connection.request(
            "POST",
            "/api/v1/control",
            body=body,
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 409
        assert payload["code"] == "process_stopped"

        process_body = json.dumps({"action": "start", "run_state": "PAUSED"})
        connection.request(
            "POST",
            "/api/v1/process",
            body=process_body,
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(process_body)),
            },
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 503
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_terminal_evidence_attestation_endpoint_dispatches(tmp_path):
    service = _service(tmp_path)
    service.apply_terminal_evidence_attestation = Mock(
        return_value={"schema_version": 1, "status": "attested"}
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
    request = {
        "confirmation": "terminal_and_strategy_unchanged_since_battle",
        "expected_active_round_identity_fingerprint": "a" * 64,
        "reason": "operator confirmation",
    }
    body = json.dumps(request)
    try:
        connection.request(
            "POST",
            "/api/v1/terminal-evidence-attestation",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["status"] == "attested"
        service.apply_terminal_evidence_attestation.assert_called_once_with(
            request
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_non_loopback_server_requires_a_strong_token(monkeypatch, tmp_path):
    monkeypatch.delenv("THETOWER_CONTROL_TOKEN", raising=False)
    assert main(
        ["--bind", "0.0.0.0", "--repository-root", str(tmp_path)]
    ) == 2

    monkeypatch.setenv("THETOWER_CONTROL_TOKEN", "too-short")
    assert main(
        ["--bind", "0.0.0.0", "--repository-root", str(tmp_path)]
    ) == 2
