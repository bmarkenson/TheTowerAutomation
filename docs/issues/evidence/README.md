# Durable Issue Evidence

This directory contains narrow, tracked evidence extracts used by issue
dossiers when the production source is subject to rolling retention. It does
not mirror runtime databases or make claims about current runtime state.

## 2026-08-07 Utility Dissonance production confirmation

[`utility-dissonance-confirmation-2026-08-07.md`](utility-dissonance-confirmation-2026-08-07.md)
preserves the exact runtime boundary, badge-shape metrics, guarded Damage
Slider inventory, terminal battle record, and privacy-safe active-to-completed
save correlation used to close `ISSUE-2026-032`.

## 2026-08-06 No Strategy attachment promotion

[`no-strategy-attachment-promotion-2026-08-06.md`](no-strategy-attachment-promotion-2026-08-06.md)
preserves the exact deployment boundary, bounded production action rows, and
privacy-safe Module check used to close `ISSUE-2026-028` and
`ISSUE-2026-029`. It also distinguishes the successful save-only attachment
from the still-open paused-manual-start confirmation and the newly exposed
unsupported primary Module index.

## 2026-07-30/31 host-performance windows

[`host-performance-2026-07-30-31-aggregates.csv`](host-performance-2026-07-30-31-aggregates.csv)
preserves the 2,194 unique aggregate rows cited by `ISSUE-2026-002` and
`ISSUE-2026-003`. Of those, 2,192 contain ten samples and two boundary flushes
contain six samples.
[`host-performance-2026-07-30-31-windows.csv`](host-performance-2026-07-30-31-windows.csv)
contains one sample-weighted summary row for each query window. The raw export
uses `evidence_windows` to record every window containing an aggregate; the
15-minute comparisons intentionally overlap the longer CPU windows.

The source was the production-generated
`/home/brianm/dev/python/TheTower/logs/host_performance.sqlite3` database,
table `host_performance_aggregates`. It was opened on 2026-08-06 through
Python's `sqlite3` URI with `mode=ro`, followed by `PRAGMA query_only = ON`.
For each window, the extraction executed:

```sql
SELECT payload_json
FROM host_performance_aggregates
WHERE window_start_utc >= ? AND window_end_utc <= ?
ORDER BY window_start_utc, aggregate_id
```

The tracked
[`export_host_performance_2026_07_30_31.py`](export_host_performance_2026_07_30_31.py)
defines the exact boundaries and expected row counts. Generation fails if any
expected row has expired. All eight expectations passed at extraction:

| Evidence window | Query start UTC | Query end UTC | Rows | Purpose |
| --- | --- | --- | ---: | --- |
| `cpu_clean_x6_3` | `2026-07-31T03:50:00+00:00` | `2026-07-31T05:02:51.799+00:00` | 433 | Stable clean x6.3 client-budget comparison |
| `cpu_long_contended_x3_5` | `2026-07-31T05:02:51.799+00:00` | `2026-07-31T08:25:49+00:00` | 1,217 | Long Death Stranding-contended interval |
| `cpu_short_contended_x3_5` | `2026-07-31T08:27:08+00:00` | `2026-07-31T09:22:58+00:00` | 334 | Complete short contended run |
| `cpu_post_contention_x3_5_5m` | `2026-07-31T09:38:30.799+00:00` | `2026-07-31T09:43:30.800+00:00` | 30 | First five post-contention minutes at x3.5 |
| `t19_long_mixed_final_15m` | `2026-07-31T08:10:41.800+00:00` | `2026-07-31T08:25:40.800+00:00` | 90 | Final 15 aggregate minutes of `Battle20260731T012549-0700` |
| `t19_short_contended_final_15m` | `2026-07-31T09:07:51.800+00:00` | `2026-07-31T09:22:50.804+00:00` | 90 | Final 15 aggregate minutes of `Battle20260731T022258-0700` |
| `t19_followup_final_15m` | `2026-07-31T10:22:51.799+00:00` | `2026-07-31T10:37:50.799+00:00` | 90 | Final 15 aggregate minutes of `Battle20260731T033754-0700` |
| `t19_next_clean_final_15m` | `2026-07-31T14:08:01.799+00:00` | `2026-07-31T14:23:00.799+00:00` | 90 | Final 15 aggregate minutes of `Battle20260731T072302-0700` |

The first four weighted summaries reproduce the cited client CPU sequence:
`0.8335%` clean, `1.9141%` and `1.9152%` contended, and `1.7611%`
post-contention. Their corresponding mean collection durations are
`50.40 ms`, `114.73 ms`, `115.12 ms`, and `61.56 ms`.

### Fields and units

The export retains table/payload identity and correlation fields
(`aggregate_id`, `session_id`, `sequence`, `host_id`, `host_name`,
`logical_processor_count`, `adb_port`, `run_id`, UTC window bounds,
`sample_count`, and `sample_interval_ms`) plus only the metrics needed by the
two dossiers:

- `host_*`, `bluestacks_*`, and `control_surface_cpu_percent_*` are percentages
  of total host capacity; `bluestacks_cpu_core_percent_*` is percent of one
  logical processor and may exceed 100.
- GPU percentages use the busiest-engine convention. Host and BlueStacks
  dedicated/shared GPU-memory fields, working/private memory, and available
  host memory are bytes.
- CPU frequency is MHz; CPU frequency ratio is current divided by maximum;
  collection duration and sample interval are milliseconds; process and sample
  fields are counts.
- `ds_*` projects only the bounded GPU-competitor entry whose process name was
  `ds`: observed sample count, busiest-engine GPU percent, and maximum
  dedicated/shared bytes. Absence remains blank rather than being rewritten as
  zero.

Window-level `*_avg` values recombine source aggregate averages weighted by
each aggregate's total `sample_count`. They are deterministic comparison
summaries, not exact raw-sample means when a source metric omitted null samples,
because per-metric valid counts were not retained. `ds_gpu_percent_avg` follows
the source's full-window convention: competitor-absent samples contribute zero,
and aggregate averages are weighted by total `sample_count`. The raw export
retains source minima and maxima. I/O, thread, handle, unrelated competitor,
ingest-context, and all out-of-window rows were deliberately not copied.
