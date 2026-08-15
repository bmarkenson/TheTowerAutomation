# Durable Issue Evidence

This directory contains narrow, tracked evidence extracts used by issue
dossiers when the production source is subject to rolling retention. It does
not mirror runtime databases or make claims about current runtime state.

## 2026-08-15 Tournament Orb Distance save alias

[`tournament-orb-distance-save-alias-2026-08-15.md`](tournament-orb-distance-save-alias-2026-08-15.md)
preserves the two exact version-1101 save/UI pairings that establish the
second Tournament `workshopOrbDistance` encoding for `ISSUE-2026-051`.

## 2026-08-15 Tournament validation repair confirmation

[`tournament-validation-repair-confirmation-2026-08-15.md`](tournament-validation-repair-confirmation-2026-08-15.md)
preserves the accepted save-carrier launch and RUNNING bindings, save-backed
session consumption without duplicate inventory UI, exact owned cleanup, and
Pause final-input denial used to close `ISSUE-2026-001`, `ISSUE-2026-046`,
`ISSUE-2026-048`, and `ISSUE-2026-050`.

## 2026-08-15 Tournament validation save-carry gap

[`tournament-save-carry-gap-2026-08-15.md`](tournament-save-carry-gap-2026-08-15.md)
preserves the complete Home save decisions, missing launch transition, safe
RUNNING invalidation, duplicate session UI, and successful owned-battle cleanup
that established `ISSUE-2026-050`.

## 2026-08-15 Tournament mapping-observation callback mismatch

[`tournament-mapping-observation-callback-2026-08-15.md`](tournament-mapping-observation-callback-2026-08-15.md)
preserves the attached Tournament preflight's unexpected-keyword failure and
the source-interface mismatch that established `ISSUE-2026-049`.

## 2026-08-15 Assist module assignment field mismatch

[`assist-module-assignment-field-2026-08-15.md`](assist-module-assignment-field-2026-08-15.md)
preserves the two post-deployment save failures, the resulting observation-only
Modules UI fallback, and a value-redacted exact-target shape inspection that
established `ISSUE-2026-048`.

## 2026-08-14 Start Battle strategy linearization failure

[`start-battle-strategy-linearization-2026-08-14.md`](start-battle-strategy-linearization-2026-08-14.md)
preserves two consecutive No Strategy selections that immediate Start Battle
rewrote to Tournament, plus the first resulting disposable validation battle
and the second workflow's pre-input rejection, for `ISSUE-2026-047`.

## 2026-08-08–13 Home Perk repair confirmation

[`perk-repair-confirmation-2026-08-08-13.md`](perk-repair-confirmation-2026-08-08-13.md)
preserves the first successful post-deployment Auto Pick correction and later
exact Ban/Auto Pick save matches used to close the original live-confirmation
limitation in `ISSUE-2026-033`.

## 2026-08-12 exclusive-validation authority mismatch

[`exclusive-validation-authority-mismatch-2026-08-12.md`](exclusive-validation-authority-mismatch-2026-08-12.md)
preserves the bounded production action sequence and repository cause that
established `ISSUE-2026-046`. It distinguishes the validation-dispatch defect
from the still-unexplained later-battle transition in `ISSUE-2026-001`.

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

## 2026-08-10/11 Death Stranding x2 correlation

[`host-performance-2026-08-10-11-aggregates.csv`](host-performance-2026-08-10-11-aggregates.csv)
preserves the 1,851 unique aggregate rows used to correlate the operator's x2
report with retained host telemetry. The overlapping
`death_stranding_observed_span` and `contended_x2_full` windows are stored once;
`evidence_windows` records both memberships.
[`host-performance-2026-08-10-11-windows.csv`](host-performance-2026-08-10-11-windows.csv)
contains one sample-weighted summary per query window.

The source was the production-generated
`/home/brianm/dev/python/TheTower/logs/host_performance.sqlite3` database,
table `host_performance_aggregates`. It was opened on 2026-08-14 through
Python's `sqlite3` URI with `mode=ro`, followed by `PRAGMA query_only = ON`.
Battle-record PDT boundaries were converted to UTC before applying the same
bounded query documented for the older extract below. The tracked
[`export_host_performance_2026_08_10_11.py`](export_host_performance_2026_08_10_11.py)
defines the boundaries and expected row counts and fails if any expected row
has expired. All four expectations passed at extraction:

| Evidence window | Query start UTC | Query end UTC | Rows | Samples | Purpose |
| --- | --- | --- | ---: | ---: | --- |
| `pre_x6_3_final_15m` | `2026-08-11T02:21:11+00:00` | `2026-08-11T02:36:11+00:00` | 89 | 890 | Final 15 minutes of the preceding complete x6.3 battle |
| `death_stranding_observed_span` | `2026-08-11T03:18:31.570+00:00` | `2026-08-11T07:57:56.554+00:00` | 1,673 | 16,692 | Complete retained interval whose every aggregate contained the `ds` process |
| `contended_x2_full` | `2026-08-11T03:38:04+00:00` | `2026-08-11T07:53:44+00:00` | 1,528 | 15,258 | Six complete same-configuration x2 battles and their short gaps |
| `post_x6_3_first_15m` | `2026-08-11T09:02:39+00:00` | `2026-08-11T09:17:39+00:00` | 89 | 890 | First 15 minutes of the first later full-length x6.3 battle |

The complete x2 window was wholly inside the observed `ds` interval. Every
one of its 1,528 aggregates and 15,258 source samples contained that process.
The boundary interval contains 16,680 `ds`-present samples out of 16,692 total
because first and last detection aggregates were partial. The comparison
summaries are:

| Window | Host CPU | Host memory | Host GPU | BlueStacks CPU | BlueStacks core CPU | `ds` GPU | Collection duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pre x6.3 final 15m | 39.07% | 85.84% | 40.63% | 9.59% | 153.42% | — | 69.67 ms |
| Contended x2 full | 82.05% | 94.93% | 76.19% | 8.63% | 138.15% | 68.58% | 193.15 ms |
| Post x6.3 first 15m | 49.10% | 81.12% | 45.48% | 13.50% | 216.07% | — | 70.85 ms |

Percentages and collection-duration fields use the units defined below.
Window averages recombine aggregate averages by `sample_count`; `ds` GPU is
weighted by `ds_sample_count`. The extract establishes process overlap and a
large host-load contrast, not causation. There is no uncontended x2 control,
and aggregate process telemetry cannot observe the game's internal update
scheduler.

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
