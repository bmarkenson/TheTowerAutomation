from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import http.client
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from core.control_directives import (
    ControlDirectiveError,
    ControlDirectiveStore,
    VALID_GAME_SPEED_TARGETS,
)
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
from tools.control_surface_server import ControlSurfaceHTTPServer, STATIC_DIR, main


def _service(root: Path, *, stale_after_seconds: int = 180) -> ControlSurfaceService:
    return ControlSurfaceService(
        repository_root=root,
        stale_after_seconds=stale_after_seconds,
    )


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

    saved = ControlDirectiveStore(path).set_strategy(
        "farm_t18",
        apply_mode="active_battle",
        source="test",
    )

    assert saved["strategy"] == "farm_t18"
    assert saved["strategy_apply_mode"] == "active_battle"
    assert ControlDirectiveStore(path).status()["strategy_apply_mode"] == (
        "active_battle"
    )


def test_control_store_rejects_unknown_strategy_apply_mode(tmp_path):
    with pytest.raises(ValueError, match="Strategy apply mode"):
        ControlDirectiveStore(tmp_path / "automation_ctl.json").set_strategy(
            "farm_t18",
            apply_mode="immediate",
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
    assert status["acknowledgements"]["state"]["acknowledges_current"]
    assert status["acknowledgements"]["mode"]["acknowledges_current"]
    assert status["acknowledgements"]["game_speed_target"]["acknowledges_current"]
    assert status["acknowledgements"]["adb_target"]["acknowledges_current"]
    assert status["acknowledgements"]["strategy"]["value"] == "farm_t18"
    assert status["acknowledgements"]["strategy"]["acknowledges_current"]


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
            "started_at": None,
            "target": "localhost:5555",
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

    response = service.apply_control({"action": "pause", "minutes": 15})
    assert response["request"] == {"accepted": True, "action": "pause"}
    control = service.control_store.read()
    assert control["state"] == "PAUSED"
    assert control["updated_by"] == "control-surface"
    assert control["resume_at"] > datetime.now().timestamp()
    assert "[CONTROL_SURFACE] Requested PAUSED for 15 minutes" in (
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
    assert "MinimumServerRevision = 20" in native_compatibility
    assert '"current_run_activity_scope"' in native_compatibility
    assert '"game_speed_target"' in native_compatibility
    assert '"host_performance_telemetry_v1"' in native_compatibility
    assert '"host_performance_gpu_v1"' in native_compatibility
    assert '"automatic_battle_attachment"' in native_compatibility
    assert '"observed_game_speed"' in native_compatibility
    assert 'id="observedSpeed"' in html
    assert 'id="gameSpeedObserved"' in html
    assert 'Content="Validate current battle if attached"' in native_xaml
    assert 'Content="Skip checks for current battle"' in native_xaml
    assert "AttachCurrentBattleBox" not in native_xaml
    assert 'Content="Use next battle"' in native_xaml
    assert 'Content="Switch this battle"' in native_xaml
    assert 'Content="Strategy profiles..."' in native_xaml
    assert '"strategy_authoring_v1"' in native_compatibility
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
    assert 'operation = "preview_rebase"' in profile_code
    assert 'reviewed_rebase_fingerprint' in profile_code
    assert 'Text="HOST HEALTH"' in native_xaml
    assert 'Text="BLUESTACKS CPU"' in native_xaml
    assert 'Text="OBSERVED SPEED"' in native_xaml


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

        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        response.read()
        assert response.status == 401

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

        body = json.dumps({"action": "resume"})
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
        assert response.status == 200
        assert payload["control"]["state"] == "RUNNING"

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


def test_non_loopback_server_requires_a_strong_token(monkeypatch, tmp_path):
    monkeypatch.delenv("THETOWER_CONTROL_TOKEN", raising=False)
    assert main(
        ["--bind", "0.0.0.0", "--repository-root", str(tmp_path)]
    ) == 2

    monkeypatch.setenv("THETOWER_CONTROL_TOKEN", "too-short")
    assert main(
        ["--bind", "0.0.0.0", "--repository-root", str(tmp_path)]
    ) == 2
