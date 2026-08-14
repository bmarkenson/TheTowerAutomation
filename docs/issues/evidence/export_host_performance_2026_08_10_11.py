#!/usr/bin/env python3
"""Export the host windows for the 2026-08-10/11 x2 contention cohort."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True)
class EvidenceWindow:
    label: str
    query_start_utc: str
    query_end_utc: str
    expected_rows: int
    purpose: str


WINDOWS = (
    EvidenceWindow(
        "pre_x6_3_final_15m",
        "2026-08-11T02:21:11+00:00",
        "2026-08-11T02:36:11+00:00",
        89,
        "Final 15 minutes of the last complete x6.3 battle before contention.",
    ),
    EvidenceWindow(
        "death_stranding_observed_span",
        "2026-08-11T03:18:31.570+00:00",
        "2026-08-11T07:57:56.554+00:00",
        1673,
        "Complete retained interval in which the ds process was observed.",
    ),
    EvidenceWindow(
        "contended_x2_full",
        "2026-08-11T03:38:04+00:00",
        "2026-08-11T07:53:44+00:00",
        1528,
        "Six complete same-configuration x2 battles and their short gaps.",
    ),
    EvidenceWindow(
        "post_x6_3_first_15m",
        "2026-08-11T09:02:39+00:00",
        "2026-08-11T09:17:39+00:00",
        89,
        "First 15 minutes of the first later full-length x6.3 battle.",
    ),
)

IDENTITY_FIELDS = (
    "aggregate_id",
    "session_id",
    "sequence",
    "host_id",
    "host_name",
    "logical_processor_count",
    "adb_port",
    "run_id",
    "window_start_utc",
    "window_end_utc",
    "sample_count",
    "sample_interval_ms",
)

METRIC_FIELDS = (
    "host_cpu_percent_avg",
    "host_cpu_percent_max",
    "host_memory_used_percent_avg",
    "host_memory_used_percent_max",
    "host_available_memory_bytes_min",
    "host_gpu_percent_avg",
    "host_gpu_percent_max",
    "bluestacks_cpu_percent_avg",
    "bluestacks_cpu_percent_max",
    "bluestacks_cpu_core_percent_avg",
    "bluestacks_cpu_core_percent_max",
    "bluestacks_working_set_bytes_avg",
    "bluestacks_working_set_bytes_max",
    "bluestacks_private_bytes_avg",
    "bluestacks_private_bytes_max",
    "bluestacks_gpu_percent_avg",
    "bluestacks_gpu_percent_max",
    "control_surface_cpu_percent_avg",
    "control_surface_cpu_percent_max",
    "sample_duration_ms_avg",
    "sample_duration_ms_max",
)

SUMMARY_METRICS = (
    "host_cpu_percent_avg",
    "host_memory_used_percent_avg",
    "host_gpu_percent_avg",
    "bluestacks_cpu_percent_avg",
    "bluestacks_cpu_core_percent_avg",
    "bluestacks_gpu_percent_avg",
    "control_surface_cpu_percent_avg",
    "sample_duration_ms_avg",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    return parser.parse_args()


def load_window(
    connection: sqlite3.Connection,
    window: EvidenceWindow,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT payload_json
        FROM host_performance_aggregates
        WHERE window_start_utc >= ? AND window_end_utc <= ?
        ORDER BY window_start_utc, aggregate_id
        """,
        (window.query_start_utc, window.query_end_utc),
    ).fetchall()
    if len(rows) != window.expected_rows:
        raise RuntimeError(
            f"{window.label}: expected {window.expected_rows} rows, "
            f"found {len(rows)}"
        )
    return [json.loads(row[0]) for row in rows]


def death_stranding_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    competitors = [
        item
        for item in payload.get("gpu_competitors", ())
        if item.get("process_name") == "ds"
    ]
    if not competitors:
        return {
            "ds_sample_count": None,
            "ds_gpu_percent_avg": None,
            "ds_gpu_percent_max": None,
            "ds_dedicated_memory_bytes_max": None,
            "ds_shared_memory_bytes_max": None,
        }
    if len(competitors) != 1:
        raise RuntimeError(
            f"{payload['aggregate_id']}: expected at most one ds competitor"
        )
    competitor = competitors[0]
    return {
        "ds_sample_count": competitor.get("sample_count"),
        "ds_gpu_percent_avg": competitor.get("gpu_percent_avg"),
        "ds_gpu_percent_max": competitor.get("gpu_percent_max"),
        "ds_dedicated_memory_bytes_max": competitor.get(
            "dedicated_memory_bytes_max"
        ),
        "ds_shared_memory_bytes_max": competitor.get("shared_memory_bytes_max"),
    }


def raw_row(
    labels: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = {"evidence_windows": labels}
    row.update({field: payload.get(field) for field in IDENTITY_FIELDS})
    metrics = payload.get("metrics", {})
    row.update({field: metrics.get(field) for field in METRIC_FIELDS})
    row.update(death_stranding_metrics(payload))
    return row


def weighted_average(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [
        (row[field], row["sample_count"])
        for row in rows
        if row.get(field) is not None
    ]
    if not values:
        return None
    return sum(value * count for value, count in values) / sum(
        count for _, count in values
    )


def summarize(
    window: EvidenceWindow,
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [raw_row(window.label, payload) for payload in payloads]
    ds_rows = [row for row in rows if row["ds_sample_count"] is not None]
    ds_samples = sum(row["ds_sample_count"] for row in ds_rows)
    summary: dict[str, Any] = {
        "evidence_window": window.label,
        "issue_ids": "ISSUE-2026-002",
        "purpose": window.purpose,
        "query_start_utc": window.query_start_utc,
        "query_end_utc": window.query_end_utc,
        "first_row_start_utc": rows[0]["window_start_utc"],
        "last_row_end_utc": rows[-1]["window_end_utc"],
        "source_row_count": len(rows),
        "source_sample_count": sum(row["sample_count"] for row in rows),
        "ds_observed_row_count": len(ds_rows),
        "ds_observed_sample_count": ds_samples,
        "ds_gpu_percent_avg": (
            sum(
                row["ds_gpu_percent_avg"] * row["ds_sample_count"]
                for row in ds_rows
            )
            / ds_samples
            if ds_samples
            else None
        ),
    }
    summary.update(
        {field: weighted_average(rows, field) for field in SUMMARY_METRICS}
    )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    connection = sqlite3.connect(
        f"file:{args.source.resolve()}?mode=ro",
        uri=True,
    )
    connection.execute("PRAGMA query_only = ON")
    try:
        loaded = {
            window.label: load_window(connection, window)
            for window in WINDOWS
        }
    finally:
        connection.close()

    labels_by_id: dict[str, set[str]] = {}
    payload_by_id: dict[str, dict[str, Any]] = {}
    for window in WINDOWS:
        for payload in loaded[window.label]:
            aggregate_id = payload["aggregate_id"]
            payload_by_id[aggregate_id] = payload
            labels_by_id.setdefault(aggregate_id, set()).add(window.label)
    aggregate_rows = [
        raw_row(";".join(sorted(labels_by_id[aggregate_id])), payload)
        for aggregate_id, payload in sorted(
            payload_by_id.items(),
            key=lambda item: (item[1]["window_start_utc"], item[0]),
        )
    ]
    summary_rows = [
        summarize(window, loaded[window.label]) for window in WINDOWS
    ]
    write_csv(
        args.output_dir / "host-performance-2026-08-10-11-aggregates.csv",
        aggregate_rows,
    )
    write_csv(
        args.output_dir / "host-performance-2026-08-10-11-windows.csv",
        summary_rows,
    )
    print(f"source_rows={len(aggregate_rows)}")
    print(f"windows={len(summary_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
