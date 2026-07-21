from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import http.client
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from core.control_directives import ControlDirectiveError, ControlDirectiveStore
from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
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
                "adb_port": 5555,
                "strategy": "farm_t18",
                "updated_at": now.isoformat(),
                "state_updated_at": now.isoformat(),
                "adb_port_updated_at": now.isoformat(),
                "strategy_updated_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "logs" / "actions.log").write_text(
        f"[INFO {earlier_timestamp}] [CTRL] Mode set to WAIT via control file\n"
        f"[INFO {timestamp}] [CTRL] State set to PAUSED via control file\n"
        f"[INFO {timestamp}] [CTRL] ADB target set to localhost:5555 via control file\n"
        f"[INFO {timestamp}] [CTRL] Strategy set to farm_t18 via control file\n"
        f"[STATUS {timestamp}] State=RUNNING/PAUSED | Wave=520 | "
        "Coins/min=1.2T | Menu=UW_MENU | Secondary=[PERKS] | "
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

    assert status["healthy"]
    assert status["control"]["state"] == "PAUSED"
    assert status["observation"] == {
        "state": "RUNNING",
        "paused": True,
        "state_label": "RUNNING/PAUSED",
        "wave": 520,
        "coins_per_minute": "1.2T",
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
    assert status["acknowledgements"]["adb_target"]["acknowledges_current"]
    assert status["acknowledgements"]["strategy"]["value"] == "farm_t18"
    assert status["acknowledgements"]["strategy"]["acknowledges_current"]


def test_status_reads_concise_heartbeat_with_paired_diagnostic_detail(tmp_path):
    now = datetime.now().astimezone().replace(microsecond=0)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = tmp_path / "logs" / "actions.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        f"[STATUS {timestamp}] State=RUNNING/PAUSED | Wave=521 | Coins/min=1.3T\n"
        f"[DEBUG {timestamp}] [STATUS_DETAIL] State=RUNNING/PAUSED | Wave=521 | "
        "Coins/min=1.3T | Menu=UW_MENU | Secondary=[PERKS] | "
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
        "menu": "UW_MENU",
        "secondary": ["PERKS"],
        "overlays": ["MENU_OPEN"],
        "observed_at": now.isoformat(timespec="seconds"),
        "age_seconds": 0,
        "stale": False,
    }
    operational = service.activity(
        levels=["STATUS", "ACTION", "INFO", "WARN", "ERROR", "FAIL"],
    )
    assert [entry["level"] for entry in operational["items"]] == ["STATUS"]
    assert [entry["level"] for entry in service.activity()["items"]] == [
        "STATUS",
        "DEBUG",
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

    with pytest.raises(ControlSurfaceRequestError):
        service.apply_control({"action": "tap", "x": 10, "y": 10})
    with pytest.raises(ControlSurfaceRequestError):
        service.apply_control({"action": "pause", "minutes": 0})


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
