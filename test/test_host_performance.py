from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import threading

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


def _write_activity_scope(root: Path, run_id: str) -> None:
    path = root / "logs" / "activity_scope.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scope": "current_run",
                "run_id": run_id,
                "started_at": "2026-07-30T14:00:00+00:00",
                "source_file_id": "1:2",
                "start_offset": 0,
            }
        ),
        encoding="utf-8",
    )


def test_host_performance_publish_is_idempotent_and_keeps_sample_run(tmp_path):
    _write_activity_scope(tmp_path, "server-run-at-ingest")
    service = ControlSurfaceService(repository_root=tmp_path)

    first = service.publish_host_performance(_request())
    duplicate = service.publish_host_performance(_request())

    assert first == {
        "schema_version": 1,
        "received": 1,
        "accepted": 1,
        "duplicates": 0,
        "ingested_at_utc": first["ingested_at_utc"],
        "server_run_id": "server-run-at-ingest",
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
    assert rows[0][:2] == ("sample-run", "server-run-at-ingest")
    stored = json.loads(rows[0][2])
    assert stored["adb_port"] == 5555
    assert stored["host_name"] == "MAIN-PC"
    assert stored["metrics"]["bluestacks_cpu_percent_avg"] == 18.75
    assert stored["gpu_competitors"] == []
    assert stored["process_attribution"] == []


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
    with pytest.raises(ControlSurfaceRequestError, match="schema_version"):
        ControlSurfaceService(
            repository_root=tmp_path
        ).publish_host_performance({"schema_version": 2, "aggregates": []})


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


def test_native_host_sampling_control_is_persistent_and_collapsible():
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
    assert 'HostPerformanceHealthState.Paused, "Sampling paused"' in tracker
    assert (
        "public bool HostPerformanceSamplingEnabled { get; set; } = true;"
        in models
    )
    assert 'x:Name="HostSamplingToggleButton"' in window
    assert 'x:Name="HostGpuText"' in window
    assert 'x:Name="BlueStacksGpuText"' in window
    assert 'x:Name="GpuCompetitorText"' in window
    assert 'x:Name="OtherWindowsCpuText"' in window
    assert 'x:Name="TopCpuProcessText"' in window
    assert 'x:Name="TopMemoryProcessText"' in window
    assert 'x:Name="ProcessAttributionStateText"' in window
    assert "snapshot.ProcessAttribution" in window_code
    assert "snapshot.OtherWindowsCpuPercent" in window_code
    assert 'Click="HostSamplingToggle_Click"' in window
    assert "TextTrimming=\"CharacterEllipsis\"" in window
    assert 'x:Name="HostHealthToggleButton"' in window
    assert 'Click="HostHealthToggle_Click"' in window
    assert 'x:Name="HostPerformancePanel"' in window
    assert "SetHostHealthExpanded" in window_code
    assert "HostPerformanceSamplingEnabled" in window_code
    assert "queued aggregates still upload" in window_code
