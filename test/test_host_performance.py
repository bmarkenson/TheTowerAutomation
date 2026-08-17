from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import pytest

from core.control_surface import ControlSurfaceRequestError, ControlSurfaceService
from core.host_performance import (
    HostPerformancePayloadError,
    validate_host_performance_request,
)
from tools.control_surface_server import ControlSurfaceHTTPServer, STATIC_DIR


PROJECT_ROOT = Path(__file__).parents[1]


def _aggregate(**overrides):
    payload = {
        "schema_version": 1,
        "aggregate_id": "26fc2dee-b4de-4956-9db1-072529e8e633",
        "session_id": "8723f378-c2f3-4b5c-896b-0a27c51f008e",
        "sequence": 4,
        "host_id": "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686",
        "host_name": "MAIN-PC",
        "logical_processor_count": 16,
        "window_start_utc": "2026-07-30T15:00:00.000+00:00",
        "window_end_utc": "2026-07-30T15:00:09.000+00:00",
        "sample_count": 10,
        "sample_interval_ms": 1000,
        "adb_port": 5555,
        "run_id": "sample-run",
        "context_observed_at_utc": "2026-07-30T15:00:05.000+00:00",
        "metrics": {
            "host_cpu_percent_avg": 32.5,
            "host_cpu_percent_max": 48.25,
            "host_memory_used_percent_avg": 61.0,
            "host_available_memory_bytes_min": 8_589_934_592,
            "bluestacks_cpu_percent_avg": 18.75,
            "bluestacks_cpu_core_percent_avg": 300.0,
            "bluestacks_working_set_bytes_avg": 4_294_967_296,
            "bluestacks_process_count_min": 3,
            "bluestacks_process_count_max": 3,
            "sample_duration_ms_avg": 1.4,
            "sample_duration_ms_max": 2.8,
        },
    }
    payload.update(overrides)
    return payload


def _request(*aggregates):
    return {
        "schema_version": 1,
        "aggregates": list(aggregates or (_aggregate(),)),
    }


def _listener(**overrides):
    payload = {
        "host_id": "ALIEN",
        "adb_port": 5555,
        "process_id": 4242,
        "process_started_at": "2026-07-30T10:00:00.1234567+00:00",
        "executable_path": (
            "C:\\Program Files\\BlueStacks_nxt\\HD-Player.exe"
        ),
        "instance_name": "Nougat32",
    }
    payload.update(overrides)
    return payload


def _gpu_competitor(**overrides):
    payload = {
        "process_id": 4242,
        "process_name": "Desktop Window Manager",
        "sample_count": 10,
        "gpu_percent_avg": 7.5,
        "gpu_percent_max": 18.25,
        "dedicated_memory_bytes_max": 268_435_456,
        "shared_memory_bytes_max": 134_217_728,
    }
    payload.update(overrides)
    return payload


def _process_attribution(**overrides):
    payload = {
        "process_id": 8080,
        "process_name": "Code",
        "sample_count": 1,
        "cpu_percent_avg": 17.5,
        "cpu_percent_max": 21.0,
        "working_set_bytes_max": 805_306_368,
        "private_bytes_max": 1_073_741_824,
    }
    payload.update(overrides)
    return payload


def test_host_performance_publish_is_idempotent_and_keeps_sample_run(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    service._runtime_battle_identity_for_host_performance = (
        lambda: "server-battle-at-ingest"
    )

    first = service.publish_host_performance(_request())
    duplicate = service.publish_host_performance(_request())

    assert first == {
        "schema_version": 1,
        "received": 1,
        "accepted": 1,
        "duplicates": 0,
        "ingested_at_utc": first["ingested_at_utc"],
        "server_run_id": "server-battle-at-ingest",
    }
    assert duplicate["accepted"] == 0
    assert duplicate["duplicates"] == 1

    with sqlite3.connect(tmp_path / "logs" / "host_performance.sqlite3") as database:
        rows = database.execute(
            """
            SELECT run_id, server_run_id_at_ingest, payload_json
            FROM host_performance_aggregates
            """
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][:2] == ("sample-run", "server-battle-at-ingest")
    stored = json.loads(rows[0][2])
    assert stored["adb_port"] == 5555
    assert stored["host_name"] == "MAIN-PC"
    assert stored["metrics"]["bluestacks_cpu_percent_avg"] == 18.75
    assert stored["bluestacks_listener"] is None
    assert stored["gpu_competitors"] == []
    assert stored["process_attribution"] == []


def test_recent_host_aggregates_are_bounded_by_run_and_cutoff(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    first = _aggregate(
        aggregate_id="cf2a62d6-427e-4476-89fd-d985c8ef3b7c",
        sequence=1,
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
        run_id="run-a",
    )
    second = _aggregate(
        aggregate_id="962a5834-154e-4ef8-b2bc-de43393ae5bf",
        sequence=2,
        window_start_utc="2026-07-30T15:00:10+00:00",
        window_end_utc="2026-07-30T15:00:19+00:00",
        run_id="run-a",
    )
    other_run = _aggregate(
        aggregate_id="a86ac09d-44ab-443b-a1aa-690b1d7ca070",
        sequence=3,
        window_start_utc="2026-07-30T15:00:20+00:00",
        window_end_utc="2026-07-30T15:00:29+00:00",
        run_id="run-b",
    )
    service.publish_host_performance(_request(first, second, other_run))

    recent = service.host_performance_store.recent_aggregates(
        run_id="run-a",
        since=datetime(2026, 7, 30, 15, 0, 10, tzinfo=timezone.utc),
    )

    assert [item["aggregate_id"] for item in recent] == [
        "962a5834-154e-4ef8-b2bc-de43393ae5bf"
    ]


def test_current_sampler_session_history_crosses_runs_but_not_target(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    same_session_prior_run = _aggregate(
        aggregate_id="11111111-1111-4111-8111-111111111111",
        sequence=1,
        run_id="prior-run",
        window_start_utc="2026-07-30T14:59:00+00:00",
        window_end_utc="2026-07-30T14:59:09+00:00",
    )
    other_session = _aggregate(
        aggregate_id="22222222-2222-4222-8222-222222222222",
        session_id="99999999-9999-4999-8999-999999999999",
        sequence=1,
        run_id="other-run",
        window_start_utc="2026-07-30T14:59:10+00:00",
        window_end_utc="2026-07-30T14:59:19+00:00",
    )
    other_port = _aggregate(
        aggregate_id="33333333-3333-4333-8333-333333333333",
        sequence=2,
        adb_port=5565,
        run_id="other-target",
        window_start_utc="2026-07-30T14:59:20+00:00",
        window_end_utc="2026-07-30T14:59:29+00:00",
    )
    current_run = _aggregate(
        aggregate_id="44444444-4444-4444-8444-444444444444",
        sequence=3,
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
    )
    service.publish_host_performance(
        _request(
            same_session_prior_run,
            other_session,
            other_port,
            current_run,
        )
    )

    history = service.host_performance_store.recent_session_aggregates(
        current_run_id="current-run",
        since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
    )

    assert [item["aggregate_id"] for item in history] == [
        "11111111-1111-4111-8111-111111111111",
        "44444444-4444-4444-8444-444444444444",
    ]


def test_exact_listener_lifetime_crosses_windows_gui_sessions(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    listener = _listener()
    prior = _aggregate(
        aggregate_id="51111111-1111-4111-8111-111111111111",
        session_id="11111111-1111-4111-8111-111111111111",
        sequence=90,
        run_id="prior-run",
        window_start_utc="2026-07-30T14:59:00+00:00",
        window_end_utc="2026-07-30T14:59:09+00:00",
        bluestacks_listener=listener,
    )
    current = _aggregate(
        aggregate_id="54444444-4444-4444-8444-444444444444",
        session_id="44444444-4444-4444-8444-444444444444",
        sequence=1,
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
        bluestacks_listener=listener,
    )
    service.publish_host_performance(_request(prior, current))

    history = (
        service.host_performance_store.recent_bluestacks_lifetime_aggregates(
            current_run_id="current-run",
            since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
        )
    )

    assert [item["aggregate_id"] for item in history] == [
        "51111111-1111-4111-8111-111111111111",
        "54444444-4444-4444-8444-444444444444",
    ]
    marker = service.host_performance_store.current_bluestacks_lifetime_marker(
        current_run_id="current-run"
    )
    assert marker is not None
    assert marker[-4:] == (
        4242,
        "2026-07-30T10:00:00.1234567+00:00",
        "C:\\Program Files\\BlueStacks_nxt\\HD-Player.exe",
        "Nougat32",
    )


def test_listener_queries_honor_explicit_selected_windows_host(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    selected_host = "13f12ca2-13af-41fc-a8bf-f4fb2fd6e686"
    other_host = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    selected = _aggregate(
        aggregate_id="55111111-1111-4111-8111-111111111111",
        host_id=selected_host,
        host_name="WORKSTATION-B",
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
        bluestacks_listener=_listener(),
        metrics={
            **_aggregate()["metrics"],
            "bluestacks_handle_count_avg": 3_100,
        },
    )
    other = _aggregate(
        aggregate_id="55444444-4444-4444-8444-444444444444",
        host_id=other_host,
        host_name="OLD-PC",
        run_id="current-run",
        window_start_utc="2026-07-30T15:01:00+00:00",
        window_end_utc="2026-07-30T15:01:09+00:00",
        bluestacks_listener=_listener(process_id=9000),
    )
    service.publish_host_performance(_request(selected, other))

    unfiltered = (
        service.host_performance_store.current_bluestacks_lifetime_marker(
            current_run_id="current-run"
        )
    )
    filtered = (
        service.host_performance_store.current_bluestacks_lifetime_marker(
            current_run_id="current-run",
            host_id=selected_host,
        )
    )
    history = (
        service.host_performance_store.recent_bluestacks_lifetime_aggregates(
            current_run_id="current-run",
            host_id=selected_host,
            since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
        )
    )
    summary = service.host_performance_store.bluestacks_lifetime_handle_summary(
        current_run_id="current-run",
        host_id=selected_host,
    )

    assert unfiltered is not None and unfiltered[0] == other_host
    assert filtered is not None and filtered[0] == selected_host
    assert filtered[4] == 4242
    assert [item["aggregate_id"] for item in history] == [
        selected["aggregate_id"]
    ]
    assert summary is not None
    assert summary["handle_low_water"] == 3_100


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("listener", "process_id", 4343),
        (
            "listener",
            "process_started_at",
            "2026-07-30T11:00:00+00:00",
        ),
        (
            "listener",
            "executable_path",
            "C:\\Other\\HD-Player.exe",
        ),
        ("listener", "instance_name", "Pie64"),
        ("listener", "adb_port", 5565),
        ("listener", "host_id", "OTHER-PC"),
        (
            "aggregate",
            "host_id",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
        ("aggregate", "adb_port", 5565),
    ],
)
def test_exact_listener_lifetime_excludes_every_identity_boundary(
    tmp_path,
    location,
    field,
    value,
):
    service = ControlSurfaceService(repository_root=tmp_path)
    prior_listener = _listener()
    prior_overrides = {}
    if location == "listener":
        prior_listener[field] = value
    else:
        prior_overrides[field] = value
    prior = _aggregate(
        aggregate_id="61111111-1111-4111-8111-111111111111",
        session_id="11111111-1111-4111-8111-111111111111",
        sequence=1,
        run_id="prior-run",
        window_start_utc="2026-07-30T14:59:00+00:00",
        window_end_utc="2026-07-30T14:59:09+00:00",
        bluestacks_listener=prior_listener,
        **prior_overrides,
    )
    current = _aggregate(
        aggregate_id="64444444-4444-4444-8444-444444444444",
        session_id="44444444-4444-4444-8444-444444444444",
        sequence=1,
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
        bluestacks_listener=_listener(),
    )
    service.publish_host_performance(_request(prior, current))

    history = (
        service.host_performance_store.recent_bluestacks_lifetime_aggregates(
            current_run_id="current-run",
            since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
        )
    )

    assert [item["aggregate_id"] for item in history] == [
        "64444444-4444-4444-8444-444444444444"
    ]


def test_unbound_current_listener_does_not_stitch_legacy_history(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    prior = _aggregate(
        aggregate_id="71111111-1111-4111-8111-111111111111",
        sequence=1,
        run_id="prior-run",
        window_start_utc="2026-07-30T14:59:00+00:00",
        window_end_utc="2026-07-30T14:59:09+00:00",
        bluestacks_listener=_listener(),
    )
    current = _aggregate(
        aggregate_id="74444444-4444-4444-8444-444444444444",
        sequence=2,
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
    )
    service.publish_host_performance(_request(prior, current))

    assert (
        service.host_performance_store.current_bluestacks_lifetime_marker(
            current_run_id="current-run"
        )
        is None
    )
    assert (
        service.host_performance_store.recent_bluestacks_lifetime_aggregates(
            current_run_id="current-run",
            since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
        )
        == []
    )


def test_listener_lifetime_preserves_distinct_runtime_and_windows_ports(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    current = _aggregate(
        run_id="current-run",
        adb_port=5556,
        bluestacks_listener=_listener(adb_port=5555),
    )
    service.publish_host_performance(_request(current))

    marker = service.host_performance_store.current_bluestacks_lifetime_marker(
        current_run_id="current-run"
    )
    assert marker is not None
    assert marker[1] == 5556
    assert marker[3] == 5555
    history = service.host_performance_store.recent_bluestacks_lifetime_aggregates(
        current_run_id="current-run",
        since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
    )
    assert len(history) == 1
    assert history[0]["aggregate_id"] == current["aggregate_id"]
    assert history[0]["adb_port"] == 5556
    assert history[0]["bluestacks_listener"]["adb_port"] == 5555


def test_listener_lifetime_handle_summary_keeps_cross_gui_low_water(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    listener = _listener()
    low = _aggregate(
        aggregate_id="81111111-1111-4111-8111-111111111111",
        session_id="11111111-1111-4111-8111-111111111111",
        sequence=1,
        run_id="prior-run",
        window_start_utc="2026-07-30T14:00:00+00:00",
        window_end_utc="2026-07-30T14:00:09+00:00",
        bluestacks_listener=listener,
        metrics={
            **_aggregate()["metrics"],
            "bluestacks_handle_count_avg": 3_100,
            "bluestacks_process_count_min": 2,
            "bluestacks_process_count_max": 2,
        },
    )
    high = _aggregate(
        aggregate_id="84444444-4444-4444-8444-444444444444",
        session_id="44444444-4444-4444-8444-444444444444",
        sequence=1,
        run_id="current-run",
        window_start_utc="2026-07-30T15:00:00+00:00",
        window_end_utc="2026-07-30T15:00:09+00:00",
        bluestacks_listener=listener,
        metrics={
            **_aggregate()["metrics"],
            "bluestacks_handle_count_avg": 27_400,
            "bluestacks_process_count_min": 2,
            "bluestacks_process_count_max": 2,
        },
    )
    service.publish_host_performance(_request(low, high))

    summary = (
        service.host_performance_store.bluestacks_lifetime_handle_summary(
            current_run_id="current-run"
        )
    )

    assert summary is not None
    assert summary["handle_low_water"] == 3_100
    assert summary["aggregate_count"] == 2
    assert summary["sampler_session_count"] == 2
    assert summary["handle_low_water_by_process_count"] == {"2": 3_100}
    assert summary["listener_identity"] == listener


def test_listener_lifetime_requires_a_valid_runtime_target_port(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    current = _aggregate(
        run_id="current-run",
        adb_port=None,
        bluestacks_listener=_listener(adb_port=5555),
    )
    service.publish_host_performance(_request(current))

    assert service.host_performance_store.current_bluestacks_lifetime_marker(
        current_run_id="current-run"
    ) is None
    assert service.host_performance_store.recent_bluestacks_lifetime_aggregates(
        current_run_id="current-run",
        since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
    ) == []


def test_nonobject_retained_listener_payload_fails_closed(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    aggregate = _aggregate(
        run_id="current-run",
        bluestacks_listener=_listener(),
    )
    service.publish_host_performance(_request(aggregate))
    with sqlite3.connect(
        tmp_path / "logs" / "host_performance.sqlite3"
    ) as database:
        database.execute(
            """
            UPDATE host_performance_aggregates
            SET payload_json = '[]'
            WHERE aggregate_id = ?
            """,
            (aggregate["aggregate_id"],),
        )

    assert (
        service.host_performance_store.current_bluestacks_lifetime_marker(
            current_run_id="current-run"
        )
        is None
    )
    assert (
        service.host_performance_store.recent_bluestacks_lifetime_aggregates(
            current_run_id="current-run",
            since=datetime(2026, 7, 30, 14, 58, tzinfo=timezone.utc),
        )
        == []
    )


def test_degradation_cache_invalidates_when_listener_lifetime_changes(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    assessment = {
        "schema_version": 1,
        "assessed_at": "2026-07-30T15:00:00+00:00",
        "status": "healthy",
        "automatic_ready": False,
        "reason": "healthy",
        "candidate_battle_ids": [],
        "baseline_battle_ids": [],
    }
    authority = {
        "runtime_battle_identity": "current-run",
        "control_model": {
            "strategy_scope": {"active_battle": "farm_t19"},
        }
    }
    with (
        patch.object(
            service.host_performance_store,
            "current_bluestacks_lifetime_marker",
            side_effect=[("old-listener",), ("new-listener",)],
        ),
        patch.object(
            service.host_performance_store,
            "recent_bluestacks_lifetime_aggregates",
            return_value=[],
        ),
        patch(
            "core.control_surface.assess_emulator_degradation",
            return_value=assessment,
        ) as assess,
    ):
        for now in (1_000.0, 1_001.0):
            service._emulator_degradation_status(
                control={"state": "RUNNING"},
                runtime_authority=authority,
                current_run={"run_id": "current-run"},
                host_maintenance={"request": None},
                now=now,
            )

    assert assess.call_count == 2


def test_host_performance_stores_gpu_metrics_and_bounded_competitors(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    aggregate = _aggregate(
        metrics={
            "host_gpu_percent_avg": 42.5,
            "host_gpu_percent_max": 77.0,
            "host_gpu_dedicated_memory_bytes_avg": 3_221_225_472,
            "bluestacks_gpu_percent_avg": 35.0,
            "bluestacks_gpu_percent_max": 68.0,
            "gpu_sample_duration_ms_avg": 0.35,
            "gpu_sample_duration_ms_max": 0.6,
        },
        gpu_competitors=[_gpu_competitor()],
    )

    response = service.publish_host_performance(_request(aggregate))

    assert response["accepted"] == 1
    with sqlite3.connect(
        tmp_path / "logs" / "host_performance.sqlite3"
    ) as database:
        payload = json.loads(
            database.execute(
                "SELECT payload_json FROM host_performance_aggregates"
            ).fetchone()[0]
        )
    assert payload["metrics"]["host_gpu_percent_avg"] == 42.5
    assert payload["gpu_competitors"] == [_gpu_competitor()]


def test_host_performance_stores_bounded_process_attribution(tmp_path):
    service = ControlSurfaceService(repository_root=tmp_path)
    aggregate = _aggregate(
        metrics={
            "host_cpu_percent_avg": 78.0,
            "process_attribution_process_count_min": 142,
            "process_attribution_process_count_max": 145,
            "process_attribution_sample_duration_ms_avg": 8.5,
            "process_attribution_sample_duration_ms_max": 9.25,
        },
        process_attribution=[_process_attribution()],
    )

    response = service.publish_host_performance(_request(aggregate))

    assert response["accepted"] == 1
    with sqlite3.connect(
        tmp_path / "logs" / "host_performance.sqlite3"
    ) as database:
        payload = json.loads(
            database.execute(
                "SELECT payload_json FROM host_performance_aggregates"
            ).fetchone()[0]
        )
    assert payload["process_attribution"] == [_process_attribution()]
    assert payload["metrics"]["process_attribution_process_count_max"] == 145


def test_host_performance_retention_prunes_old_aggregates(tmp_path):
    service = ControlSurfaceService(
        repository_root=tmp_path,
        host_performance_retention_days=2,
    )
    old = _aggregate(
        aggregate_id="cf2a62d6-427e-4476-89fd-d985c8ef3b7c",
        window_start_utc="2026-07-20T15:00:00+00:00",
        window_end_utc="2026-07-20T15:00:09+00:00",
    )
    service.host_performance_store.publish(
        _request(old),
        server_run_id=None,
        now=datetime(2026, 7, 20, 15, 1, tzinfo=timezone.utc),
    )
    recent = _aggregate(
        aggregate_id="962a5834-154e-4ef8-b2bc-de43393ae5bf",
        window_start_utc="2026-07-23T15:00:00+00:00",
        window_end_utc="2026-07-23T15:00:09+00:00",
    )
    service.host_performance_store.publish(
        _request(recent),
        server_run_id=None,
        now=datetime(2026, 7, 23, 15, 1, tzinfo=timezone.utc),
    )

    with sqlite3.connect(tmp_path / "logs" / "host_performance.sqlite3") as database:
        aggregate_ids = {
            row[0]
            for row in database.execute(
                "SELECT aggregate_id FROM host_performance_aggregates"
            )
        }
    assert aggregate_ids == {"962a5834-154e-4ef8-b2bc-de43393ae5bf"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda aggregate: aggregate.update(
                metrics={"host_cpu_percent_avg": float("nan")}
            ),
            "must be finite",
        ),
        (
            lambda aggregate: aggregate.update(adb_port=0),
            "must be between 1 and 65535",
        ),
        (
            lambda aggregate: aggregate.update(run_id="../../escape"),
            "bounded run identifier",
        ),
        (
            lambda aggregate: aggregate.update(
                bluestacks_listener=_listener(process_id=0)
            ),
            "must be between 1 and 2147483647",
        ),
        (
            lambda aggregate: aggregate.update(
                bluestacks_listener=_listener(
                    process_started_at=(
                        "2026-07-30T10:00:00."
                        + "1" * 50
                        + "+00:00"
                    )
                )
            ),
            "at most 64 characters",
        ),
        (
            lambda aggregate: aggregate.update(
                gpu_competitors=[
                    _gpu_competitor(gpu_percent_avg=40.0, gpu_percent_max=20.0)
                ]
            ),
            "must not exceed",
        ),
        (
            lambda aggregate: aggregate.update(
                gpu_competitors=[_gpu_competitor(sample_count=11)]
            ),
            "must be between 1 and 10",
        ),
        (
            lambda aggregate: aggregate.update(
                gpu_competitors=[
                    _gpu_competitor(process_id=index + 1)
                    for index in range(6)
                ]
            ),
            "at most 5",
        ),
        (
            lambda aggregate: aggregate.update(
                process_attribution=[
                    _process_attribution(
                        cpu_percent_avg=30.0,
                        cpu_percent_max=20.0,
                    )
                ]
            ),
            "must not exceed",
        ),
        (
            lambda aggregate: aggregate.update(
                process_attribution=[
                    _process_attribution(cpu_percent_avg=None)
                ]
            ),
            "must both be null or numeric",
        ),
        (
            lambda aggregate: aggregate.update(
                process_attribution=[
                    _process_attribution(process_id=index + 1)
                    for index in range(9)
                ]
            ),
            "at most 8",
        ),
        (
            lambda aggregate: aggregate.update(
                window_end_utc=(
                    datetime(2026, 7, 30, 15, tzinfo=timezone.utc)
                    + timedelta(minutes=6)
                ).isoformat()
            ),
            "must not exceed five minutes",
        ),
    ],
)
def test_host_performance_rejects_invalid_aggregate(mutation, message):
    aggregate = _aggregate()
    mutation(aggregate)

    with pytest.raises(HostPerformancePayloadError, match=message):
        validate_host_performance_request(_request(aggregate))


def test_control_surface_translates_host_performance_payload_error(tmp_path):
    with pytest.raises(
        ControlSurfaceRequestError,
        match="schema_version",
    ) as failure:
        ControlSurfaceService(
            repository_root=tmp_path
        ).publish_host_performance({"schema_version": 2, "aggregates": []})
    assert failure.value.code == "invalid_host_performance_request"
    assert failure.value.details == {}


def test_host_performance_rejection_identifies_only_invalid_aggregate(tmp_path):
    invalid = _aggregate(
        aggregate_id="120f6782-cbb5-4656-aac4-1ca12a9a62f5",
        sequence=5,
        metrics={"unsupported_metric": 1.0},
    )

    with pytest.raises(ControlSurfaceRequestError) as failure:
        ControlSurfaceService(repository_root=tmp_path).publish_host_performance(
            _request(_aggregate(), invalid)
        )

    assert failure.value.code == "invalid_host_performance_aggregate"
    assert failure.value.details == {"aggregate_index": 1}
    assert "unsupported_metric" in str(failure.value)


def test_http_host_performance_endpoint_requires_token_and_accepts_batch(tmp_path):
    server = ControlSurfaceHTTPServer(
        ("127.0.0.1", 0),
        service=ControlSurfaceService(repository_root=tmp_path),
        token="test-secret",
        static_dir=STATIC_DIR,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=3,
    )
    body = json.dumps(_request())
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    try:
        connection.request(
            "POST",
            "/api/v1/host-performance",
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 401

        connection.request(
            "POST",
            "/api/v1/host-performance",
            body=body,
            headers={**headers, "Authorization": "Bearer test-secret"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["accepted"] == 1
        assert payload["duplicates"] == 0

        invalid_body = json.dumps(
            _request(_aggregate(metrics={"unsupported_metric": 1.0}))
        )
        connection.request(
            "POST",
            "/api/v1/host-performance",
            body=invalid_body,
            headers={
                **headers,
                "Content-Length": str(len(invalid_body)),
                "Authorization": "Bearer test-secret",
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert payload["code"] == "invalid_host_performance_aggregate"
        assert payload["details"] == {"aggregate_index": 0}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_native_sampler_keeps_expensive_process_launches_out_of_sample_path():
    native_root = PROJECT_ROOT / "windows" / "TheTower.ControlSurface"
    sampler = (native_root / "WindowsHostPerformanceSampler.cs").read_text(
        encoding="utf-8"
    )
    tracker = (native_root / "HostPerformanceTracker.cs").read_text(
        encoding="utf-8"
    )
    gpu_sampler = (native_root / "WindowsGpuPerformanceSampler.cs").read_text(
        encoding="utf-8"
    )
    process_attribution = (
        native_root / "HostProcessAttribution.cs"
    ).read_text(encoding="utf-8")

    assert "ThreadPriority.BelowNormal" in tracker
    assert "SampleIntervalMilliseconds = 1000" in tracker
    assert "AggregateSampleCount = 10" in tracker
    assert "RawRingCapacity = 120" in tracker
    assert "_samplingStateChanged" in tracker
    assert "FlushAggregateWindow(aggregateWindow)" in tracker
    assert "ProcessDiscoveryIntervalSamples = 10" in sampler
    assert "BlueStacksHandleCount" in sampler
    assert "RefreshBlueStacksListener" in sampler
    assert "BlueStacksListener = first.BlueStacksListener" in tracker
    assert "GetSystemTimes" in sampler
    assert "GlobalMemoryStatusEx" in sampler
    assert "GetProcessIoCounters" in sampler
    assert "PdhCollectQueryData" in gpu_sampler
    assert "PdhFormatNoScale" in gpu_sampler
    assert "private uint _capacity" in gpu_sampler
    assert r"\GPU Engine(*)\Utilization Percentage" in gpu_sampler
    assert r"\GPU Process Memory(*)\Dedicated Usage" in gpu_sampler
    assert "MaximumSampleCompetitors = 8" in gpu_sampler
    assert "MaximumGpuCompetitors = 5" in tracker
    assert "MaximumSelectedProcesses = 8" in process_attribution
    assert "HostMemoryThresholdPercent = 95.0" in process_attribution
    assert "ActivationDelay = TimeSpan.FromSeconds(30)" in process_attribution
    assert "RecoveryDelay = TimeSpan.FromMinutes(2)" in process_attribution
    assert "process_attribution_sample_duration_ms" in tracker
    assert "Process.Start(" not in sampler
    assert "Process.Start(" not in gpu_sampler
    assert "Process.GetProcesses(" not in gpu_sampler
    assert "PowerShell" not in sampler
    assert "PowerShell" not in gpu_sampler
    assert "nvidia-smi" not in sampler
    assert "nvidia-smi" not in gpu_sampler


def test_native_host_sampling_control_is_persistent_and_unambiguous():
    native_root = PROJECT_ROOT / "windows" / "TheTower.ControlSurface"
    tracker = (native_root / "HostPerformanceTracker.cs").read_text(
        encoding="utf-8"
    )
    models = (native_root / "Models.cs").read_text(encoding="utf-8")
    window = (native_root / "MainWindow.xaml").read_text(encoding="utf-8")
    window_code = (native_root / "MainWindow.xaml.cs").read_text(
        encoding="utf-8"
    )

    assert "public bool SamplingEnabled" in tracker
    assert "public void SetSamplingEnabled(bool enabled)" in tracker
    assert "_sampler.ResetRateBaselines();" in tracker
    assert 'HostPerformanceHealthState.Paused, "Sampling off"' in tracker
    assert (
        "public bool HostPerformanceSamplingEnabled { get; set; } = true;"
        in models
    )
    assert 'x:Name="HostSamplingToggleButton"' in window
    assert 'Content="Stop sampling"' in window
    assert "Pause sampling" not in window
    assert 'x:Name="HostGpuText"' in window
    assert 'x:Name="BlueStacksGpuText"' in window
    assert 'x:Name="BlueStacksHandlesText"' in window
    assert 'x:Name="BlueStacksHostEvidenceText"' in window
    assert 'x:Name="BlueStacksRestartButton"' in window
    assert 'Click="BlueStacksRestart_Click"' in window
    assert 'x:Name="GpuCompetitorText"' in window
    assert 'x:Name="OtherWindowsCpuText"' in window
    assert 'x:Name="TopCpuProcessText"' in window
    assert 'x:Name="TopMemoryProcessText"' in window
    assert 'x:Name="ProcessAttributionStateText"' in window
    assert "snapshot.ProcessAttribution" in window_code
    assert "snapshot.OtherWindowsCpuPercent" in window_code
    assert "snapshot.BlueStacksHandleCount" in window_code
    assert "degradation.HostEvidence" in window_code
    assert "RequestOperatorRestartAsync" in window_code
    assert "independent gem and reward collectors continue" in window_code
    assert "normally replays 5 waves" not in window_code
    assert "_blueStacksOperatorMessage" in window_code
    assert "SetBlueStacksOperatorMessage" in window_code
    assert "ResolveTelemetryTarget" in window_code
    assert "ReconcileTunnelHostBlueStacksPort" in window_code
    refresh_status = window_code.split(
        "private async Task RefreshStatusAsync", 1
    )[1].split("private async Task ObserveBlueStacksMaintenanceAsync", 1)[0]
    assert refresh_status.index("ReconcileTunnelHostBlueStacksPort(status)") < (
        refresh_status.index("RenderStatus(status)")
    ) < refresh_status.index("QueueBlueStacksMaintenance(status)")
    assert "RequestOutcomeUnknown" in window_code
    assert "allowRequestCreation: false" in window_code

    controller = (native_root / "BlueStacksInstanceController.cs").read_text(
        encoding="utf-8"
    )
    assert "QueryFullProcessImageName" in controller
    assert "ProcessAccessRights.QueryLimitedInformation" in controller
    assert "GetProcessTimes" in controller
    assert ".MainModule" not in controller
    assert "_shutdownStarted = false;" in window_code
    assert "terminal_disposition" in models
    assert "terminal_reason" in models
    assert "request.TerminalDisposition" in window_code
    assert "request.TerminalReason" in window_code
    assert "evidenceParts.AddRange(identityParts)" in window_code
    assert "if (_shutdownStarted)" in window_code
    assert 'Click="HostSamplingToggle_Click"' in window
    assert "TextTrimming=\"CharacterEllipsis\"" in window
    assert 'x:Name="HostHealthToggleButton"' in window
    assert 'Click="HostHealthToggle_Click"' in window
    assert 'x:Name="HostPerformancePanel"' in window
    assert "SetHostHealthExpanded" in window_code
    assert "HostPerformanceSamplingEnabled" in window_code
    assert '? "Stop sampling"' in window_code
    assert ': "Start sampling";' in window_code
    queue_presentation = window_code.split(
        "HostTelemetryQueueText.Text = ", maxsplit=1
    )[1].split(";\n", maxsplit=1)[0]
    assert queue_presentation.lstrip().startswith("!snapshot.SamplingEnabled")
    assert '"Sampling off"' in queue_presentation
    assert "uploader draining" in queue_presentation
    assert queue_presentation.index('"Sampling off"') < queue_presentation.index(
        '"Buffering"'
    )
    queue_status_block = window_code.split(
        "HostTelemetryQueueText.Text = ", maxsplit=1
    )[1].split("var details =", maxsplit=1)[0]
    assert "RejectedAggregateCount" not in queue_status_block
    assert "snapshot.UploadError" in queue_status_block
    assert '" · upload issue"' in queue_status_block
    assert "Retained rejection history:" in window_code
    assert "does not by " in window_code
    assert "itself indicate a current upload problem." in window_code
    assert "Host sampling is off; the independent uploader" in window_code
    assert "Sampling paused" not in tracker
    assert "Pause sampling" not in window_code
    assert "Resume sampling" not in window_code
