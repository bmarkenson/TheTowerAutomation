"""Validated, idempotent storage for Windows host-performance aggregates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID


HOST_PERFORMANCE_SCHEMA_VERSION = 1
MAX_HOST_PERFORMANCE_BATCH = 120
MAX_GPU_COMPETITORS = 5
MAX_PROCESS_ATTRIBUTION = 8
DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS = 30

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_METRIC_LIMITS: dict[str, tuple[float, float]] = {
    "host_cpu_percent_avg": (0.0, 100.0),
    "host_cpu_percent_max": (0.0, 100.0),
    "host_memory_used_percent_avg": (0.0, 100.0),
    "host_memory_used_percent_max": (0.0, 100.0),
    "host_available_memory_bytes_min": (0.0, float(2**63 - 1)),
    "host_cpu_frequency_mhz_avg": (0.0, 1_000_000.0),
    "host_cpu_frequency_mhz_min": (0.0, 1_000_000.0),
    "host_cpu_frequency_ratio_avg": (0.0, 10.0),
    "host_cpu_frequency_ratio_min": (0.0, 10.0),
    "bluestacks_cpu_percent_avg": (0.0, 100.0),
    "bluestacks_cpu_percent_max": (0.0, 100.0),
    "bluestacks_cpu_core_percent_avg": (0.0, 100_000.0),
    "bluestacks_cpu_core_percent_max": (0.0, 100_000.0),
    "bluestacks_working_set_bytes_avg": (0.0, float(2**63 - 1)),
    "bluestacks_working_set_bytes_max": (0.0, float(2**63 - 1)),
    "bluestacks_private_bytes_avg": (0.0, float(2**63 - 1)),
    "bluestacks_private_bytes_max": (0.0, float(2**63 - 1)),
    "bluestacks_io_read_bytes_per_second_avg": (0.0, float(2**63 - 1)),
    "bluestacks_io_read_bytes_per_second_max": (0.0, float(2**63 - 1)),
    "bluestacks_io_write_bytes_per_second_avg": (0.0, float(2**63 - 1)),
    "bluestacks_io_write_bytes_per_second_max": (0.0, float(2**63 - 1)),
    "bluestacks_process_count_min": (0.0, 10_000.0),
    "bluestacks_process_count_max": (0.0, 10_000.0),
    "bluestacks_thread_count_avg": (0.0, 1_000_000.0),
    "bluestacks_thread_count_max": (0.0, 1_000_000.0),
    "bluestacks_handle_count_avg": (0.0, 10_000_000.0),
    "bluestacks_handle_count_max": (0.0, 10_000_000.0),
    "host_gpu_percent_avg": (0.0, 100.0),
    "host_gpu_percent_max": (0.0, 100.0),
    "host_gpu_dedicated_memory_bytes_avg": (0.0, float(2**63 - 1)),
    "host_gpu_dedicated_memory_bytes_max": (0.0, float(2**63 - 1)),
    "host_gpu_shared_memory_bytes_avg": (0.0, float(2**63 - 1)),
    "host_gpu_shared_memory_bytes_max": (0.0, float(2**63 - 1)),
    "bluestacks_gpu_percent_avg": (0.0, 100.0),
    "bluestacks_gpu_percent_max": (0.0, 100.0),
    "bluestacks_gpu_dedicated_memory_bytes_avg": (
        0.0,
        float(2**63 - 1),
    ),
    "bluestacks_gpu_dedicated_memory_bytes_max": (
        0.0,
        float(2**63 - 1),
    ),
    "bluestacks_gpu_shared_memory_bytes_avg": (0.0, float(2**63 - 1)),
    "bluestacks_gpu_shared_memory_bytes_max": (0.0, float(2**63 - 1)),
    "gpu_process_count_min": (0.0, 100_000.0),
    "gpu_process_count_max": (0.0, 100_000.0),
    "gpu_sample_duration_ms_avg": (0.0, 60_000.0),
    "gpu_sample_duration_ms_max": (0.0, 60_000.0),
    "process_attribution_process_count_min": (0.0, 100_000.0),
    "process_attribution_process_count_max": (0.0, 100_000.0),
    "process_attribution_sample_duration_ms_avg": (0.0, 60_000.0),
    "process_attribution_sample_duration_ms_max": (0.0, 60_000.0),
    "control_surface_cpu_percent_avg": (0.0, 100.0),
    "control_surface_cpu_percent_max": (0.0, 100.0),
    "sample_duration_ms_avg": (0.0, 60_000.0),
    "sample_duration_ms_max": (0.0, 60_000.0),
}


class HostPerformancePayloadError(ValueError):
    """A rejected host-performance payload."""


class HostPerformanceStorageError(RuntimeError):
    """Host-performance telemetry could not be persisted."""


class HostPerformanceStore:
    """Store bounded aggregate batches with durable aggregate-id deduplication."""

    def __init__(
        self,
        path: Path | str,
        *,
        retention_days: int = DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS,
    ) -> None:
        self.path = Path(path).resolve()
        self.retention_days = max(1, int(retention_days))
        self._write_lock = threading.Lock()

    def publish(
        self,
        request: Mapping[str, Any],
        *,
        server_run_id: Optional[str],
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        aggregates = validate_host_performance_request(request)
        received_at = _utc_datetime(now or datetime.now(timezone.utc))
        received_at_text = received_at.isoformat(timespec="milliseconds")
        normalized_server_run_id = _optional_run_id(
            server_run_id,
            field="server_run_id",
        )

        accepted = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._write_lock:
                with sqlite3.connect(self.path, timeout=10.0) as connection:
                    self._prepare(connection)
                    for aggregate in aggregates:
                        stored = dict(aggregate)
                        stored["ingested_at_utc"] = received_at_text
                        stored["server_run_id_at_ingest"] = normalized_server_run_id
                        cursor = connection.execute(
                            """
                            INSERT OR IGNORE INTO host_performance_aggregates (
                                aggregate_id,
                                session_id,
                                sequence,
                                host_id,
                                host_name,
                                adb_port,
                                run_id,
                                window_start_utc,
                                window_end_utc,
                                ingested_at_utc,
                                server_run_id_at_ingest,
                                payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                aggregate["aggregate_id"],
                                aggregate["session_id"],
                                aggregate["sequence"],
                                aggregate["host_id"],
                                aggregate["host_name"],
                                aggregate.get("adb_port"),
                                aggregate.get("run_id"),
                                aggregate["window_start_utc"],
                                aggregate["window_end_utc"],
                                received_at_text,
                                normalized_server_run_id,
                                json.dumps(
                                    stored,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    allow_nan=False,
                                ),
                            ),
                        )
                        accepted += cursor.rowcount
                    cutoff = received_at - timedelta(days=self.retention_days)
                    connection.execute(
                        """
                        DELETE FROM host_performance_aggregates
                        WHERE window_end_utc < ?
                        """,
                        (cutoff.isoformat(timespec="milliseconds"),),
                    )
        except (OSError, sqlite3.Error) as exc:
            raise HostPerformanceStorageError(str(exc)) from exc

        received = len(aggregates)
        return {
            "schema_version": HOST_PERFORMANCE_SCHEMA_VERSION,
            "received": received,
            "accepted": accepted,
            "duplicates": received - accepted,
            "ingested_at_utc": received_at_text,
            "server_run_id": normalized_server_run_id,
        }

    @staticmethod
    def _prepare(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS host_performance_aggregates (
                aggregate_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                host_id TEXT NOT NULL,
                host_name TEXT NOT NULL,
                adb_port INTEGER,
                run_id TEXT,
                window_start_utc TEXT NOT NULL,
                window_end_utc TEXT NOT NULL,
                ingested_at_utc TEXT NOT NULL,
                server_run_id_at_ingest TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS host_performance_by_run
            ON host_performance_aggregates (run_id, window_start_utc)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS host_performance_by_host_time
            ON host_performance_aggregates (host_id, window_start_utc)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS host_performance_by_window_end
            ON host_performance_aggregates (window_end_utc)
            """
        )


def validate_host_performance_request(
    request: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(request, Mapping):
        raise HostPerformancePayloadError("Request body must be a JSON object")
    if request.get("schema_version") != HOST_PERFORMANCE_SCHEMA_VERSION:
        raise HostPerformancePayloadError(
            "host-performance schema_version must be 1"
        )
    if set(request) != {"schema_version", "aggregates"}:
        raise HostPerformancePayloadError(
            "host-performance request must define schema_version and aggregates"
        )
    aggregates = request.get("aggregates")
    if not isinstance(aggregates, Sequence) or isinstance(
        aggregates,
        (str, bytes, bytearray),
    ):
        raise HostPerformancePayloadError("aggregates must be a JSON array")
    if not 1 <= len(aggregates) <= MAX_HOST_PERFORMANCE_BATCH:
        raise HostPerformancePayloadError(
            f"aggregates must contain 1 to {MAX_HOST_PERFORMANCE_BATCH} items"
        )
    return [
        _validate_aggregate(aggregate, index=index)
        for index, aggregate in enumerate(aggregates)
    ]


def _validate_aggregate(
    aggregate: object,
    *,
    index: int,
) -> dict[str, Any]:
    if not isinstance(aggregate, Mapping):
        raise HostPerformancePayloadError(
            f"aggregates[{index}] must be a JSON object"
        )
    required = {
        "schema_version",
        "aggregate_id",
        "session_id",
        "sequence",
        "host_id",
        "host_name",
        "logical_processor_count",
        "window_start_utc",
        "window_end_utc",
        "sample_count",
        "sample_interval_ms",
        "adb_port",
        "run_id",
        "context_observed_at_utc",
        "metrics",
    }
    optional = {"gpu_competitors", "process_attribution"}
    provided = set(aggregate)
    if not required.issubset(provided) or provided - required - optional:
        missing = sorted(required - provided)
        extra = sorted(provided - required - optional)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise HostPerformancePayloadError(
            f"aggregates[{index}] has invalid fields ({'; '.join(detail)})"
        )
    if aggregate.get("schema_version") != HOST_PERFORMANCE_SCHEMA_VERSION:
        raise HostPerformancePayloadError(
            f"aggregates[{index}].schema_version must be 1"
        )

    normalized = dict(aggregate)
    normalized["aggregate_id"] = _uuid_text(
        aggregate.get("aggregate_id"),
        field=f"aggregates[{index}].aggregate_id",
    )
    normalized["session_id"] = _uuid_text(
        aggregate.get("session_id"),
        field=f"aggregates[{index}].session_id",
    )
    normalized["host_id"] = _uuid_text(
        aggregate.get("host_id"),
        field=f"aggregates[{index}].host_id",
    )
    normalized["host_name"] = _bounded_text(
        aggregate.get("host_name"),
        field=f"aggregates[{index}].host_name",
        maximum=128,
    )
    normalized["sequence"] = _bounded_integer(
        aggregate.get("sequence"),
        field=f"aggregates[{index}].sequence",
        minimum=0,
        maximum=2**63 - 1,
    )
    normalized["logical_processor_count"] = _bounded_integer(
        aggregate.get("logical_processor_count"),
        field=f"aggregates[{index}].logical_processor_count",
        minimum=1,
        maximum=4096,
    )
    normalized["sample_count"] = _bounded_integer(
        aggregate.get("sample_count"),
        field=f"aggregates[{index}].sample_count",
        minimum=1,
        maximum=120,
    )
    normalized["sample_interval_ms"] = _bounded_integer(
        aggregate.get("sample_interval_ms"),
        field=f"aggregates[{index}].sample_interval_ms",
        minimum=250,
        maximum=10_000,
    )
    normalized["adb_port"] = _optional_integer(
        aggregate.get("adb_port"),
        field=f"aggregates[{index}].adb_port",
        minimum=1,
        maximum=65_535,
    )
    normalized["run_id"] = _optional_run_id(
        aggregate.get("run_id"),
        field=f"aggregates[{index}].run_id",
    )

    window_start = _utc_timestamp(
        aggregate.get("window_start_utc"),
        field=f"aggregates[{index}].window_start_utc",
    )
    window_end = _utc_timestamp(
        aggregate.get("window_end_utc"),
        field=f"aggregates[{index}].window_end_utc",
    )
    if window_end < window_start:
        raise HostPerformancePayloadError(
            f"aggregates[{index}].window_end_utc precedes window_start_utc"
        )
    if window_end - window_start > timedelta(minutes=5):
        raise HostPerformancePayloadError(
            f"aggregates[{index}] window must not exceed five minutes"
        )
    normalized["window_start_utc"] = window_start.isoformat(
        timespec="milliseconds"
    )
    normalized["window_end_utc"] = window_end.isoformat(
        timespec="milliseconds"
    )
    context_observed_at = aggregate.get("context_observed_at_utc")
    normalized["context_observed_at_utc"] = (
        None
        if context_observed_at is None
        else _utc_timestamp(
            context_observed_at,
            field=f"aggregates[{index}].context_observed_at_utc",
        ).isoformat(timespec="milliseconds")
    )

    metrics = aggregate.get("metrics")
    if not isinstance(metrics, Mapping):
        raise HostPerformancePayloadError(
            f"aggregates[{index}].metrics must be a JSON object"
        )
    unknown_metrics = sorted(set(metrics) - set(_METRIC_LIMITS))
    if unknown_metrics:
        raise HostPerformancePayloadError(
            f"aggregates[{index}].metrics contains unsupported fields: "
            + ", ".join(unknown_metrics)
        )
    if not metrics:
        raise HostPerformancePayloadError(
            f"aggregates[{index}].metrics must not be empty"
        )
    normalized_metrics: dict[str, float] = {}
    for name, value in metrics.items():
        minimum, maximum = _METRIC_LIMITS[name]
        normalized_metrics[name] = _bounded_number(
            value,
            field=f"aggregates[{index}].metrics.{name}",
            minimum=minimum,
            maximum=maximum,
        )
    normalized["metrics"] = normalized_metrics
    normalized["gpu_competitors"] = _validate_gpu_competitors(
        aggregate.get("gpu_competitors", []),
        field=f"aggregates[{index}].gpu_competitors",
        maximum_samples=normalized["sample_count"],
    )
    normalized["process_attribution"] = _validate_process_attribution(
        aggregate.get("process_attribution", []),
        field=f"aggregates[{index}].process_attribution",
        maximum_samples=normalized["sample_count"],
    )
    return normalized


def _validate_gpu_competitors(
    value: object,
    *,
    field: str,
    maximum_samples: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise HostPerformancePayloadError(f"{field} must be a JSON array")
    if len(value) > MAX_GPU_COMPETITORS:
        raise HostPerformancePayloadError(
            f"{field} must contain at most {MAX_GPU_COMPETITORS} items"
        )

    required = {
        "process_id",
        "process_name",
        "sample_count",
        "gpu_percent_avg",
        "gpu_percent_max",
        "dedicated_memory_bytes_max",
        "shared_memory_bytes_max",
    }
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[int, str]] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, Mapping) or set(item) != required:
            raise HostPerformancePayloadError(
                f"{item_field} must define exactly "
                + ", ".join(sorted(required))
            )
        process_id = _bounded_integer(
            item.get("process_id"),
            field=f"{item_field}.process_id",
            minimum=1,
            maximum=2**31 - 1,
        )
        process_name = _bounded_text(
            item.get("process_name"),
            field=f"{item_field}.process_name",
            maximum=128,
        )
        identity = (process_id, process_name.casefold())
        if identity in identities:
            raise HostPerformancePayloadError(
                f"{field} contains duplicate process identity "
                + f"{process_name} ({process_id})"
            )
        identities.add(identity)
        gpu_average = _bounded_number(
            item.get("gpu_percent_avg"),
            field=f"{item_field}.gpu_percent_avg",
            minimum=0.0,
            maximum=100.0,
        )
        gpu_maximum = _bounded_number(
            item.get("gpu_percent_max"),
            field=f"{item_field}.gpu_percent_max",
            minimum=0.0,
            maximum=100.0,
        )
        if gpu_average > gpu_maximum:
            raise HostPerformancePayloadError(
                f"{item_field}.gpu_percent_avg must not exceed "
                "gpu_percent_max"
            )
        normalized.append(
            {
                "process_id": process_id,
                "process_name": process_name,
                "sample_count": _bounded_integer(
                    item.get("sample_count"),
                    field=f"{item_field}.sample_count",
                    minimum=1,
                    maximum=maximum_samples,
                ),
                "gpu_percent_avg": gpu_average,
                "gpu_percent_max": gpu_maximum,
                "dedicated_memory_bytes_max": _bounded_integer(
                    item.get("dedicated_memory_bytes_max"),
                    field=f"{item_field}.dedicated_memory_bytes_max",
                    minimum=0,
                    maximum=2**63 - 1,
                ),
                "shared_memory_bytes_max": _bounded_integer(
                    item.get("shared_memory_bytes_max"),
                    field=f"{item_field}.shared_memory_bytes_max",
                    minimum=0,
                    maximum=2**63 - 1,
                ),
            }
        )
    return normalized


def _validate_process_attribution(
    value: object,
    *,
    field: str,
    maximum_samples: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise HostPerformancePayloadError(f"{field} must be a JSON array")
    if len(value) > MAX_PROCESS_ATTRIBUTION:
        raise HostPerformancePayloadError(
            f"{field} must contain at most {MAX_PROCESS_ATTRIBUTION} items"
        )

    required = {
        "process_id",
        "process_name",
        "sample_count",
        "cpu_percent_avg",
        "cpu_percent_max",
        "working_set_bytes_max",
        "private_bytes_max",
    }
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[int, str]] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, Mapping) or set(item) != required:
            raise HostPerformancePayloadError(
                f"{item_field} must define exactly "
                + ", ".join(sorted(required))
            )
        process_id = _bounded_integer(
            item.get("process_id"),
            field=f"{item_field}.process_id",
            minimum=1,
            maximum=2**31 - 1,
        )
        process_name = _bounded_text(
            item.get("process_name"),
            field=f"{item_field}.process_name",
            maximum=128,
        )
        identity = (process_id, process_name.casefold())
        if identity in identities:
            raise HostPerformancePayloadError(
                f"{field} contains duplicate process identity "
                + f"{process_name} ({process_id})"
            )
        identities.add(identity)

        cpu_average_value = item.get("cpu_percent_avg")
        cpu_maximum_value = item.get("cpu_percent_max")
        if (cpu_average_value is None) != (cpu_maximum_value is None):
            raise HostPerformancePayloadError(
                f"{item_field} CPU average and maximum must both be null "
                "or numeric"
            )
        cpu_average = (
            None
            if cpu_average_value is None
            else _bounded_number(
                cpu_average_value,
                field=f"{item_field}.cpu_percent_avg",
                minimum=0.0,
                maximum=100.0,
            )
        )
        cpu_maximum = (
            None
            if cpu_maximum_value is None
            else _bounded_number(
                cpu_maximum_value,
                field=f"{item_field}.cpu_percent_max",
                minimum=0.0,
                maximum=100.0,
            )
        )
        if (
            cpu_average is not None
            and cpu_maximum is not None
            and cpu_average > cpu_maximum
        ):
            raise HostPerformancePayloadError(
                f"{item_field}.cpu_percent_avg must not exceed "
                "cpu_percent_max"
            )
        normalized.append(
            {
                "process_id": process_id,
                "process_name": process_name,
                "sample_count": _bounded_integer(
                    item.get("sample_count"),
                    field=f"{item_field}.sample_count",
                    minimum=1,
                    maximum=maximum_samples,
                ),
                "cpu_percent_avg": cpu_average,
                "cpu_percent_max": cpu_maximum,
                "working_set_bytes_max": _bounded_integer(
                    item.get("working_set_bytes_max"),
                    field=f"{item_field}.working_set_bytes_max",
                    minimum=0,
                    maximum=2**63 - 1,
                ),
                "private_bytes_max": _bounded_integer(
                    item.get("private_bytes_max"),
                    field=f"{item_field}.private_bytes_max",
                    minimum=0,
                    maximum=2**63 - 1,
                ),
            }
        )
    return normalized


def _uuid_text(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HostPerformancePayloadError(f"{field} must be a UUID") from exc


def _bounded_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise HostPerformancePayloadError(f"{field} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 for character in normalized)
    ):
        raise HostPerformancePayloadError(
            f"{field} must contain 1 to {maximum} printable characters"
        )
    return normalized


def _bounded_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HostPerformancePayloadError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise HostPerformancePayloadError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _optional_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> Optional[int]:
    return (
        None
        if value is None
        else _bounded_integer(
            value,
            field=field,
            minimum=minimum,
            maximum=maximum,
        )
    )


def _bounded_number(
    value: object,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HostPerformancePayloadError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise HostPerformancePayloadError(
            f"{field} must be finite and between {minimum} and {maximum}"
        )
    return number


def _optional_run_id(value: object, *, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value.strip()):
        raise HostPerformancePayloadError(
            f"{field} must be null or a bounded run identifier"
        )
    return value.strip()


def _utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise HostPerformancePayloadError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HostPerformancePayloadError(
            f"{field} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise HostPerformancePayloadError(f"{field} must include a timezone")
    return _utc_datetime(parsed)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_HOST_PERFORMANCE_RETENTION_DAYS",
    "HOST_PERFORMANCE_SCHEMA_VERSION",
    "HostPerformancePayloadError",
    "HostPerformanceStorageError",
    "HostPerformanceStore",
    "MAX_HOST_PERFORMANCE_BATCH",
    "MAX_GPU_COMPETITORS",
    "validate_host_performance_request",
]
