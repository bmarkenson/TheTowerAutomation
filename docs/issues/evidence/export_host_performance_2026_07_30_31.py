#!/usr/bin/env python3
"""Export the narrow host-performance windows cited by 2026 issue dossiers."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


@dataclass(frozen=True)
class EvidenceWindow:
    label: str
    issue_ids: str
    query_start_utc: str
    query_end_utc: str
    expected_rows: int
    purpose: str


WINDOWS = (
    EvidenceWindow(
        "cpu_clean_x6_3",
        "ISSUE-2026-003",
        "2026-07-31T03:50:00+00:00",
        "2026-07-31T05:02:51.799+00:00",
        433,
        "Stable clean x6.3 interval before Death Stranding first appeared.",
    ),
    EvidenceWindow(
        "cpu_long_contended_x3_5",
        "ISSUE-2026-002;ISSUE-2026-003",
        "2026-07-31T05:02:51.799+00:00",
        "2026-07-31T08:25:49+00:00",
        1217,
        "Long mixed run while Death Stranding was observed and speed was x3.5.",
    ),
    EvidenceWindow(
        "cpu_short_contended_x3_5",
        "ISSUE-2026-002;ISSUE-2026-003",
        "2026-07-31T08:27:08+00:00",
        "2026-07-31T09:22:58+00:00",
        334,
        "Complete short x3.5 run while Death Stranding was observed.",
    ),
    EvidenceWindow(
        "cpu_post_contention_x3_5_5m",
        "ISSUE-2026-003",
        "2026-07-31T09:38:30.799+00:00",
        "2026-07-31T09:43:30.800+00:00",
        30,
        "First five minutes after Death Stranding disappeared while speed remained x3.5.",
    ),
    EvidenceWindow(
        "t19_long_mixed_final_15m",
        "ISSUE-2026-002",
        "2026-07-31T08:10:41.800+00:00",
        "2026-07-31T08:25:40.800+00:00",
        90,
        "Final 15 aggregate minutes of Battle20260731T012549-0700.",
    ),
    EvidenceWindow(
        "t19_short_contended_final_15m",
        "ISSUE-2026-002",
        "2026-07-31T09:07:51.800+00:00",
        "2026-07-31T09:22:50.804+00:00",
        90,
        "Final 15 aggregate minutes of Battle20260731T022258-0700.",
    ),
    EvidenceWindow(
        "t19_followup_final_15m",
        "ISSUE-2026-002",
        "2026-07-31T10:22:51.799+00:00",
        "2026-07-31T10:37:50.799+00:00",
        90,
        "Final 15 aggregate minutes of Battle20260731T033754-0700.",
    ),
    EvidenceWindow(
        "t19_next_clean_final_15m",
        "ISSUE-2026-002",
        "2026-07-31T14:08:01.799+00:00",
        "2026-07-31T14:23:00.799+00:00",
        90,
        "Final 15 aggregate minutes of Battle20260731T072302-0700.",
    ),
)

IDENTITY_FIELDS = (
    "aggregate_id", "session_id", "sequence", "host_id", "host_name",
    "logical_processor_count", "adb_port", "run_id", "window_start_utc",
    "window_end_utc", "sample_count", "sample_interval_ms",
)

METRIC_FIELDS = (
    "host_cpu_percent_avg", "host_cpu_percent_max",
    "host_memory_used_percent_avg", "host_memory_used_percent_max",
    "host_available_memory_bytes_min", "host_cpu_frequency_mhz_avg",
    "host_cpu_frequency_mhz_min", "host_cpu_frequency_ratio_avg",
    "host_cpu_frequency_ratio_min", "host_gpu_percent_avg",
    "host_gpu_percent_max", "host_gpu_dedicated_memory_bytes_avg",
    "host_gpu_dedicated_memory_bytes_max",
    "host_gpu_shared_memory_bytes_avg", "host_gpu_shared_memory_bytes_max",
    "bluestacks_cpu_percent_avg", "bluestacks_cpu_percent_max",
    "bluestacks_cpu_core_percent_avg", "bluestacks_cpu_core_percent_max",
    "bluestacks_working_set_bytes_avg", "bluestacks_working_set_bytes_max",
    "bluestacks_private_bytes_avg", "bluestacks_private_bytes_max",
    "bluestacks_process_count_min", "bluestacks_process_count_max",
    "bluestacks_gpu_percent_avg", "bluestacks_gpu_percent_max",
    "bluestacks_gpu_dedicated_memory_bytes_avg",
    "bluestacks_gpu_dedicated_memory_bytes_max",
    "bluestacks_gpu_shared_memory_bytes_avg",
    "bluestacks_gpu_shared_memory_bytes_max",
    "control_surface_cpu_percent_avg", "control_surface_cpu_percent_max",
    "sample_duration_ms_avg", "sample_duration_ms_max",
)

DS_FIELDS = (
    "ds_sample_count", "ds_gpu_percent_avg", "ds_gpu_percent_max",
    "ds_dedicated_memory_bytes_max", "ds_shared_memory_bytes_max",
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


def ds_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    competitors = [
        item
        for item in payload.get("gpu_competitors", ())
        if item.get("process_name") == "ds"
    ]
    if not competitors:
        return {field: None for field in DS_FIELDS}
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


def raw_row(payload: dict[str, Any], labels: Iterable[str]) -> dict[str, Any]:
    row = {"evidence_windows": ";".join(sorted(labels))}
    row.update({field: payload.get(field) for field in IDENTITY_FIELDS})
    metrics = payload.get("metrics", {})
    row.update({field: metrics.get(field) for field in METRIC_FIELDS})
    row.update(ds_metrics(payload))
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


def summarize(window: EvidenceWindow, rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_rows = [raw_row(payload, (window.label,)) for payload in rows]
    ds_values = [
        (row["ds_gpu_percent_avg"], row["ds_sample_count"])
        for row in raw_rows
        if row.get("ds_gpu_percent_avg") is not None
        and row.get("ds_sample_count") is not None
    ]
    return {
        "evidence_window": window.label,
        "issue_ids": window.issue_ids,
        "purpose": window.purpose,
        "query_start_utc": window.query_start_utc,
        "query_end_utc": window.query_end_utc,
        "first_row_start_utc": rows[0]["window_start_utc"],
        "last_row_end_utc": rows[-1]["window_end_utc"],
        "source_row_count": len(rows),
        "source_sample_count": sum(row["sample_count"] for row in rows),
        "control_surface_cpu_percent_avg": weighted_average(
            raw_rows, "control_surface_cpu_percent_avg"
        ),
        "sample_duration_ms_avg": weighted_average(
            raw_rows, "sample_duration_ms_avg"
        ),
        "host_cpu_percent_avg": weighted_average(raw_rows, "host_cpu_percent_avg"),
        "host_memory_used_percent_avg": weighted_average(
            raw_rows, "host_memory_used_percent_avg"
        ),
        "host_gpu_percent_avg": weighted_average(raw_rows, "host_gpu_percent_avg"),
        "host_gpu_dedicated_memory_bytes_avg": weighted_average(
            raw_rows, "host_gpu_dedicated_memory_bytes_avg"
        ),
        "bluestacks_cpu_percent_avg": weighted_average(
            raw_rows, "bluestacks_cpu_percent_avg"
        ),
        "bluestacks_working_set_bytes_avg": weighted_average(
            raw_rows, "bluestacks_working_set_bytes_avg"
        ),
        "bluestacks_gpu_percent_avg": weighted_average(
            raw_rows, "bluestacks_gpu_percent_avg"
        ),
        "ds_observed_row_count": len(ds_values),
        "ds_observed_sample_count": sum(count for _, count in ds_values),
        "ds_gpu_percent_avg": (
            sum(value * count for value, count in ds_values)
            / sum(count for _, count in ds_values)
            if ds_values
            else None
        ),
    }


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
        raw_row(payload, labels_by_id[aggregate_id])
        for aggregate_id, payload in sorted(
            payload_by_id.items(),
            key=lambda item: (item[1]["window_start_utc"], item[0]),
        )
    ]
    summary_rows = [summarize(window, loaded[window.label]) for window in WINDOWS]
    write_csv(
        args.output_dir / "host-performance-2026-07-30-31-aggregates.csv",
        aggregate_rows,
    )
    write_csv(
        args.output_dir / "host-performance-2026-07-30-31-windows.csv",
        summary_rows,
    )
    print(f"source_rows={len(aggregate_rows)}")
    print(f"windows={len(summary_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
