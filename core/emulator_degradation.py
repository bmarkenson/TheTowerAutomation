"""Conservative read model for BlueStacks aging and Farm-run degradation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence

from core.battle_classification import classification_for_record
from core.battle_stats import included_in_default_history, parse_tower_number


DEGRADATION_SCHEMA_VERSION = 1
MINIMUM_BASELINE_RUNS = 3
MAXIMUM_BASELINE_RUNS = 5
CANDIDATE_RUNS = 2
INDIVIDUAL_CPH_RATIO = Decimal("0.93")
PAIR_CPH_RATIO = Decimal("0.90")
MINIMUM_SPEED_RATIO = Decimal("0.97")
MINIMUM_HOST_WINDOWS = 96
MINIMUM_HOST_SPAN = timedelta(minutes=16)
MINIMUM_HOST_COVERAGE_SECONDS = Decimal("960")
RECENT_HOST_WINDOW = timedelta(minutes=20)
MINIMUM_HANDLE_RATIO = Decimal("1.8")
MINIMUM_HANDLE_DELTA = Decimal("4000")
AUTOMATIC_RESTART_COOLDOWN = timedelta(hours=8)
PREVENTIVE_HANDLE_CEILING = Decimal("25000")
PREVENTIVE_HANDLE_DELTA = Decimal("10000")
PREVENTIVE_WINDOW = timedelta(minutes=10)
PREVENTIVE_MINIMUM_COVERAGE_SECONDS = Decimal("600")
SEVERE_INTERVAL_COUNT = 3
SEVERE_CPH_RATIO = Decimal("0.60")
SEVERE_MINIMUM_BASELINE_SAMPLES = 6
SEVERE_MINIMUM_BASELINE_RUNS = 2
SEVERE_WAVE_BAND_SIZE = 1000
SEVERE_MAXIMUM_CHECKPOINT_AGE = timedelta(minutes=25)
CONTENTION_WINDOW = timedelta(minutes=15)
CONTENTION_MINIMUM_COVERAGE_SECONDS = Decimal("600")
EXTERNAL_CPU_PERCENT = Decimal("40")
EXTERNAL_GPU_PERCENT = Decimal("30")
EXTERNAL_MEMORY_PERCENT = Decimal("75")


def load_comparable_battles(
    battles_dir: Path,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Load bounded newest representative Farm records without source bulk."""

    requested_limit = max(1, min(int(limit), 100))
    records: list[dict[str, Any]] = []
    for path in sorted(
        battles_dir.glob("Battle*.json"),
        key=lambda candidate: candidate.name,
        reverse=True,
    )[: max(100, requested_limit * 5)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not included_in_default_history(payload):
            continue
        classification = classification_for_record(payload)
        if str(classification.get("type") or "").lower() != "farm":
            continue
        runtime = payload.get("runtime")
        if isinstance(runtime, Mapping) and isinstance(
            runtime.get("emulator_recovery"), Mapping
        ):
            # Preserve raw report history, but never calibrate the detector from
            # a run that paid restart downtime or a non-earning replay window.
            continue
        cph = _report_decimal(payload, "coins_per_hour")
        speed = _decimal_at(payload, "derived", "effective_game_speed")
        strategy = str(payload.get("strategy") or "").strip().lower()
        configuration = payload.get("run_configuration")
        if cph is None or cph <= 0 or speed is None or speed <= 0 or not strategy:
            continue
        records.append(
            {
                "battle_id": str(payload.get("battle_id") or path.stem),
                "captured_at": str(payload.get("captured_at") or ""),
                "strategy": strategy,
                "configuration_fingerprint": _fingerprint(
                    configuration if isinstance(configuration, Mapping) else {}
                ),
                "coins_per_hour": cph,
                "effective_game_speed": speed,
                "strategy_definition_fingerprint": str(
                    runtime.get("strategy_definition_fingerprint") or ""
                )
                if isinstance(runtime, Mapping)
                else "",
                **_active_run_interval_history(payload),
            }
        )
        if len(records) >= requested_limit:
            break
    records.sort(key=_record_timestamp, reverse=True)
    return records[:requested_limit]


def assess_emulator_degradation(
    battles: Sequence[Mapping[str, Any]],
    host_aggregates: Sequence[Mapping[str, Any]],
    *,
    current_strategy: Optional[str],
    current_run_id: Optional[str],
    active_run_performance: Optional[Mapping[str, Any]] = None,
    lifetime_handle_summary: Optional[Mapping[str, Any]] = None,
    assessed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assess legacy, preventive, and severe in-run recovery lanes."""

    legacy = _assess_completed_run_degradation(
        battles,
        host_aggregates,
        current_strategy=current_strategy,
        current_run_id=current_run_id,
        lifetime_handle_summary=lifetime_handle_summary,
        assessed_at=assessed_at,
    )
    when = _utc_timestamp(legacy["assessed_at"])
    host = legacy["host_evidence"]
    contention = _contention_evidence(host_aggregates, when=when)
    completed = {
        "status": "ready" if legacy.get("automatic_ready") is True else str(
            legacy.get("status") or "unavailable"
        ),
        "ready": legacy.get("automatic_ready") is True,
        "reason": str(legacy.get("reason") or ""),
    }
    preventive = _preventive_handle_lane(
        host_aggregates,
        host=host,
        lifetime_handle_summary=lifetime_handle_summary,
        contention=contention,
        when=when,
    )
    severe = _severe_in_run_lane(
        battles,
        active_run_performance=active_run_performance,
        host=host,
        contention=contention,
        when=when,
    )
    return {
        **legacy,
        "host_contention": contention,
        "automatic_triggers": {
            "preventive_handle_ceiling": preventive,
            "severe_in_run_loss": severe,
            "completed_run_degradation": completed,
        },
    }


def _assess_completed_run_degradation(
    battles: Sequence[Mapping[str, Any]],
    host_aggregates: Sequence[Mapping[str, Any]],
    *,
    current_strategy: Optional[str],
    current_run_id: Optional[str],
    lifetime_handle_summary: Optional[Mapping[str, Any]] = None,
    assessed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assess two comparable runs plus exact-listener handle growth."""

    when = (assessed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    host = _host_evidence(
        host_aggregates,
        when=when,
        lifetime_handle_summary=lifetime_handle_summary,
    )
    base = {
        "schema_version": DEGRADATION_SCHEMA_VERSION,
        "assessed_at": when.isoformat(timespec="seconds"),
        "current_run_id": current_run_id,
        "current_strategy": str(current_strategy or "").strip().lower() or None,
        "automatic_ready": False,
        "candidate_battle_ids": [],
        "baseline_battle_ids": [],
        "host_evidence": host,
    }
    strategy = str(current_strategy or "").strip().lower()
    if not strategy or strategy in {"none", "tournament"}:
        return {
            **base,
            "status": "ineligible",
            "reason": "automatic recovery is limited to an active Farm strategy",
        }
    exact = [
        dict(record)
        for record in battles
        if str(record.get("strategy") or "").lower() == strategy
    ]
    if len(exact) < CANDIDATE_RUNS + MINIMUM_BASELINE_RUNS:
        return {
            **base,
            "status": "insufficient_history",
            "reason": "at least five comparable completed Farm runs are required",
        }
    fingerprint = str(exact[0].get("configuration_fingerprint") or "")
    exact = [
        record
        for record in exact
        if str(record.get("configuration_fingerprint") or "") == fingerprint
    ]
    if len(exact) < CANDIDATE_RUNS + MINIMUM_BASELINE_RUNS:
        return {
            **base,
            "status": "insufficient_history",
            "reason": "exact run-configuration history is too small",
        }
    candidates = exact[:CANDIDATE_RUNS]
    baselines = exact[
        CANDIDATE_RUNS : CANDIDATE_RUNS + MAXIMUM_BASELINE_RUNS
    ]
    baseline_cph = _median_decimal(
        record.get("coins_per_hour") for record in baselines
    )
    baseline_speed = _median_decimal(
        record.get("effective_game_speed") for record in baselines
    )
    candidate_cph = _median_decimal(
        record.get("coins_per_hour") for record in candidates
    )
    candidate_speed = _median_decimal(
        record.get("effective_game_speed") for record in candidates
    )
    if None in {baseline_cph, baseline_speed, candidate_cph, candidate_speed}:
        return {
            **base,
            "status": "insufficient_history",
            "reason": "comparable CPH or effective-speed evidence is unavailable",
        }
    assert baseline_cph is not None
    assert baseline_speed is not None
    assert candidate_cph is not None
    assert candidate_speed is not None
    cph_ratios = [
        _decimal(record.get("coins_per_hour")) / baseline_cph
        for record in candidates
    ]
    pair_ratio = candidate_cph / baseline_cph
    speed_ratio = candidate_speed / baseline_speed
    evidence = {
        **base,
        "candidate_battle_ids": [
            str(record.get("battle_id") or "") for record in candidates
        ],
        "baseline_battle_ids": [
            str(record.get("battle_id") or "") for record in baselines
        ],
        "configuration_fingerprint": fingerprint,
        "baseline_coins_per_hour": str(baseline_cph),
        "candidate_coins_per_hour": str(candidate_cph),
        "candidate_cph_ratio": float(pair_ratio),
        "individual_cph_ratios": [float(value) for value in cph_ratios],
        "effective_game_speed_ratio": float(speed_ratio),
    }
    degraded = bool(
        all(value <= INDIVIDUAL_CPH_RATIO for value in cph_ratios)
        and pair_ratio <= PAIR_CPH_RATIO
        and speed_ratio >= MINIMUM_SPEED_RATIO
    )
    if not degraded:
        return {
            **evidence,
            "status": "healthy",
            "reason": "completed comparable runs do not meet degradation thresholds",
        }

    if host["status"] == "saturated":
        return {
            **evidence,
            "status": "deferred_host_contention",
            "reason": "host saturation makes emulator aging ambiguous",
        }
    if host["status"] != "confirmed_growth":
        return {
            **evidence,
            "status": "recommend",
            "reason": (
                "two comparable Farm runs are degraded, but sustained exact-"
                "listener host corroboration is incomplete"
            ),
        }
    return {
        **evidence,
        "status": "automatic_ready",
        "automatic_ready": True,
        "reason": (
            "two comparable Farm runs have depressed CPH at normal effective "
            "speed and BlueStacks handle growth is sustained"
        ),
    }


def _preventive_handle_lane(
    aggregates: Sequence[Mapping[str, Any]],
    *,
    host: Mapping[str, Any],
    lifetime_handle_summary: Optional[Mapping[str, Any]],
    contention: Mapping[str, Any],
    when: datetime,
) -> dict[str, Any]:
    """Return a sustained absolute handle-ceiling decision."""

    listener = host.get("listener_identity")
    lifetime_listener = (
        lifetime_handle_summary.get("listener_identity")
        if isinstance(lifetime_handle_summary, Mapping)
        else None
    )
    if (
        host.get("identity_scope") != "exact_listener_lifetime"
        or not isinstance(listener, Mapping)
        or not isinstance(lifetime_listener, Mapping)
        or dict(listener) != dict(lifetime_listener)
    ):
        return {
            "status": "insufficient",
            "ready": False,
            "reason": "the exact listener-lifetime low-water is unavailable",
        }
    samples = _recent_handle_samples(
        aggregates,
        since=when - PREVENTIVE_WINDOW,
    )
    coverage = sum((sample[2] for sample in samples), Decimal(0))
    if coverage < PREVENTIVE_MINIMUM_COVERAGE_SECONDS:
        return {
            "status": "insufficient",
            "ready": False,
            "sampled_coverage_seconds": float(coverage),
            "reason": "fewer than ten sampled minutes are available",
        }
    stable_coverage = sum(
        (sample[2] for sample in samples if sample[3]),
        Decimal(0),
    )
    process_counts = {sample[4] for sample in samples if sample[3]}
    if (
        stable_coverage < coverage * Decimal("0.9")
        or len(process_counts) != 1
    ):
        return {
            "status": "unstable_process_set",
            "ready": False,
            "sampled_coverage_seconds": float(coverage),
            "reason": "the recent BlueStacks process set was not stable",
        }
    recent = _median_decimal(sample[1] for sample in samples)
    current_process_count = next(iter(process_counts))
    low_water_by_process_count = lifetime_handle_summary.get(
        "handle_low_water_by_process_count"
    )
    low_water = (
        _decimal(
            low_water_by_process_count.get(str(int(current_process_count)))
        )
        if isinstance(low_water_by_process_count, Mapping)
        else None
    )
    if recent is None or low_water is None:
        return {
            "status": "insufficient",
            "ready": False,
            "reason": (
                "handle-level evidence for the current stable process set is "
                "incomplete"
            ),
        }
    delta = recent - low_water
    threshold_met = bool(
        recent >= PREVENTIVE_HANDLE_CEILING
        and delta >= PREVENTIVE_HANDLE_DELTA
    )
    deferred = bool(
        threshold_met and contention.get("status") != "clear"
    )
    return {
        "status": (
            "ready_contended"
            if threshold_met and contention.get("status") == "external_contention"
            else "ready_ambiguous"
            if deferred
            else "ready"
            if threshold_met
            else "below_threshold"
        ),
        "ready": threshold_met,
        "deferred_by_contention": deferred,
        "sample_count": len(samples),
        "bluestacks_process_count": int(current_process_count),
        "sampled_coverage_seconds": float(coverage),
        "handle_ceiling": float(PREVENTIVE_HANDLE_CEILING),
        "required_handle_delta": float(PREVENTIVE_HANDLE_DELTA),
        "handle_recent_median": float(recent),
        "handle_low_water": float(low_water),
        "handle_delta": float(delta),
        "contention_status": contention.get("status"),
        "reason": (
            "the sustained handle ceiling is met, but external contention is present"
            if threshold_met
            and contention.get("status") == "external_contention"
            else "the sustained handle ceiling is met, but contention attribution is incomplete"
            if deferred
            else "the sustained exact-listener handle ceiling is met"
            if threshold_met
            else "the sustained handle level remains below the preventive ceiling"
        ),
    }


def _severe_in_run_lane(
    battles: Sequence[Mapping[str, Any]],
    *,
    active_run_performance: Optional[Mapping[str, Any]],
    host: Mapping[str, Any],
    contention: Mapping[str, Any],
    when: datetime,
) -> dict[str, Any]:
    """Compare three current save intervals with a tolerant exact regime."""

    if not isinstance(active_run_performance, Mapping):
        return {
            "status": "insufficient",
            "ready": False,
            "reason": "save-backed active-run performance is unavailable",
        }
    strategy = str(active_run_performance.get("strategy") or "").lower()
    configuration = str(
        active_run_performance.get("configuration_fingerprint") or ""
    )
    definition = str(
        active_run_performance.get("strategy_definition_fingerprint") or ""
    )
    mapping_id = str(active_run_performance.get("mapping_id") or "")
    semantic = str(
        active_run_performance.get("semantic_fingerprint") or ""
    )
    checkpoints = active_run_performance.get("checkpoints")
    if (
        not strategy
        or not configuration
        or not mapping_id
        or not semantic
        or not isinstance(checkpoints, Sequence)
        or isinstance(checkpoints, (str, bytes, bytearray))
    ):
        return {
            "status": "insufficient",
            "ready": False,
            "reason": "the active performance regime is not exactly bound",
        }
    candidates = _performance_intervals(checkpoints)[-SEVERE_INTERVAL_COUNT:]
    if len(candidates) < SEVERE_INTERVAL_COUNT:
        return {
            "status": "insufficient",
            "ready": False,
            "interval_count": len(candidates),
            "reason": "three valid save-backed intervals are required",
        }
    candidates.sort(key=lambda item: item["captured_at"])
    if (
        candidates[-1]["captured_at"] > when + timedelta(minutes=2)
        or
        when - candidates[-1]["captured_at"] > SEVERE_MAXIMUM_CHECKPOINT_AGE
        or any(
            later["captured_at"] <= earlier["captured_at"]
            or later["captured_at"] - earlier["captured_at"]
            > timedelta(minutes=12)
            or later["wave"] < earlier["wave"]
            for earlier, later in zip(candidates, candidates[1:])
        )
    ):
        return {
            "status": "stale_or_discontinuous",
            "ready": False,
            "reason": "the recent save-backed intervals are stale or discontinuous",
        }

    comparable = [
        record
        for record in battles
        if str(record.get("strategy") or "").lower() == strategy
        and str(record.get("configuration_fingerprint") or "") == configuration
        and str(record.get("metric_mapping_id") or "") == mapping_id
        and str(record.get("metric_semantic_fingerprint") or "") == semantic
        and (
            not definition
            or not str(record.get("strategy_definition_fingerprint") or "")
            or str(record.get("strategy_definition_fingerprint") or "")
            == definition
        )
    ]
    ratios: list[Decimal] = []
    speed_ratios: list[Decimal] = []
    baseline_floors: list[Decimal] = []
    baseline_run_ids: set[str] = set()
    for candidate in candidates:
        band = candidate["wave"] // SEVERE_WAVE_BAND_SIZE
        baseline: list[dict[str, Any]] = []
        band_run_ids: set[str] = set()
        for record in comparable:
            record_id = str(record.get("battle_id") or "")
            for interval in _performance_intervals(
                record.get("metric_intervals") or ()
            ):
                if interval["wave"] // SEVERE_WAVE_BAND_SIZE == band:
                    baseline.append(interval)
                    if record_id:
                        band_run_ids.add(record_id)
                        baseline_run_ids.add(record_id)
        if (
            len(baseline) < SEVERE_MINIMUM_BASELINE_SAMPLES
            or len(band_run_ids) < SEVERE_MINIMUM_BASELINE_RUNS
        ):
            return {
                "status": "insufficient_baseline",
                "ready": False,
                "wave_band": band,
                "baseline_sample_count": len(baseline),
                "baseline_run_count": len(band_run_ids),
                "reason": "the broad wave band lacks a multi-run baseline",
            }
        floor = _lower_envelope(item["coins_per_hour"] for item in baseline)
        baseline_speed = _median_decimal(
            item["effective_game_speed"] for item in baseline
        )
        assert floor is not None and baseline_speed is not None
        baseline_floors.append(floor)
        ratios.append(candidate["coins_per_hour"] / floor)
        speed_ratios.append(candidate["effective_game_speed"] / baseline_speed)

    evidence = {
        "interval_count": len(candidates),
        "baseline_run_ids": sorted(baseline_run_ids),
        "baseline_floor_coins_per_hour": [
            str(value) for value in baseline_floors
        ],
        "interval_cph_ratios": [float(value) for value in ratios],
        "effective_game_speed_ratios": [
            float(value) for value in speed_ratios
        ],
        "severe_cph_ratio": float(SEVERE_CPH_RATIO),
        "wave_band_size": SEVERE_WAVE_BAND_SIZE,
    }
    if any(value < MINIMUM_SPEED_RATIO for value in speed_ratios):
        return {
            **evidence,
            "status": "speed_degraded",
            "ready": False,
            "reason": "effective game speed is below the comparable baseline",
        }
    if any(value > SEVERE_CPH_RATIO for value in ratios):
        return {
            **evidence,
            "status": "within_relaxed_band",
            "ready": False,
            "reason": "recent CPH remains inside the relaxed healthy envelope",
        }
    if contention.get("status") != "clear":
        return {
            **evidence,
            "status": "deferred_host_contention",
            "ready": False,
            "reason": (
                "external host contention can explain the severe CPH loss"
                if contention.get("status") == "external_contention"
                else "host contention attribution is incomplete"
            ),
        }
    if host.get("status") != "confirmed_growth":
        return {
            **evidence,
            "status": "missing_handle_corroboration",
            "ready": False,
            "reason": "severe CPH loss lacks sustained BlueStacks handle growth",
        }
    return {
        **evidence,
        "status": "ready",
        "ready": True,
        "reason": (
            "three save-backed intervals are catastrophically below a relaxed "
            "same-regime wave-band baseline with normal speed and handle growth"
        ),
    }


def _contention_evidence(
    aggregates: Sequence[Mapping[str, Any]],
    *,
    when: datetime,
) -> dict[str, Any]:
    """Attribute sustained recent load outside BlueStacks."""

    total_coverage = Decimal(0)
    attributed_coverage = Decimal(0)
    external_coverage = Decimal(0)
    other_cpu_values: list[Decimal] = []
    other_gpu_values: list[Decimal] = []
    other_memory_values: list[Decimal] = []
    reasons: set[str] = set()
    for aggregate in aggregates:
        metrics = aggregate.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        try:
            ended = _utc_timestamp(aggregate.get("window_end_utc"))
        except (TypeError, ValueError):
            continue
        if ended < when - CONTENTION_WINDOW:
            continue
        count = _decimal(aggregate.get("sample_count"))
        interval = _decimal(aggregate.get("sample_interval_ms"))
        if count is None or interval is None:
            continue
        coverage = count * interval / Decimal(1000)
        if coverage <= 0:
            continue
        total_coverage += coverage
        host_cpu = _decimal(metrics.get("host_cpu_percent_avg"))
        bluestacks_cpu = _decimal(metrics.get("bluestacks_cpu_percent_avg"))
        controller_cpu = _decimal(
            metrics.get("control_surface_cpu_percent_avg")
        )
        host_gpu = _decimal(metrics.get("host_gpu_percent_avg"))
        bluestacks_gpu = _decimal(metrics.get("bluestacks_gpu_percent_avg"))
        memory = _decimal(metrics.get("host_memory_used_percent_avg"))
        available = _decimal(metrics.get("host_available_memory_bytes_min"))
        bluestacks_memory = _decimal(
            metrics.get("bluestacks_working_set_bytes_avg")
        )
        if None in {
            host_cpu,
            bluestacks_cpu,
            controller_cpu,
            host_gpu,
            bluestacks_gpu,
            memory,
            available,
            bluestacks_memory,
        }:
            continue
        assert host_cpu is not None
        assert bluestacks_cpu is not None
        assert controller_cpu is not None
        assert host_gpu is not None
        assert bluestacks_gpu is not None
        assert memory is not None
        assert available is not None
        assert bluestacks_memory is not None
        other_cpu = max(
            Decimal(0),
            host_cpu - bluestacks_cpu - controller_cpu,
        )
        other_gpu = max(Decimal(0), host_gpu - bluestacks_gpu)
        available_fraction = Decimal(1) - memory / Decimal(100)
        if available_fraction <= 0:
            continue
        total_memory = available / available_fraction
        if total_memory <= 0:
            continue
        attributed_coverage += coverage
        used_memory = max(Decimal(0), total_memory - available)
        other_memory = max(Decimal(0), used_memory - bluestacks_memory)
        other_memory_percent = (
            other_memory * Decimal(100) / total_memory
            if total_memory > 0
            else Decimal(0)
        )
        other_cpu_values.append(other_cpu)
        other_gpu_values.append(other_gpu)
        other_memory_values.append(other_memory_percent)
        external = False
        if other_cpu >= EXTERNAL_CPU_PERCENT:
            reasons.add("sustained_other_cpu")
            external = True
        if other_gpu >= EXTERNAL_GPU_PERCENT:
            reasons.add("sustained_other_gpu")
            external = True
        if memory >= 92 and other_memory_percent >= EXTERNAL_MEMORY_PERCENT:
            reasons.add("external_memory_pressure")
            external = True
        if (
            available <= Decimal(1024**3)
            and other_memory_percent >= EXTERNAL_MEMORY_PERCENT
        ):
            reasons.add("external_low_available_memory")
            external = True
        frequency = _decimal(metrics.get("host_cpu_frequency_ratio_min"))
        if frequency is not None and frequency < Decimal("0.75") and host_cpu >= 70:
            reasons.add("host_cpu_throttling")
            external = True
        if external:
            external_coverage += coverage
    common = {
        "sampled_coverage_seconds": float(total_coverage),
        "attributed_coverage_seconds": float(attributed_coverage),
        "other_cpu_percent_median": (
            float(_median_decimal(other_cpu_values))
            if other_cpu_values
            else None
        ),
        "other_gpu_percent_median": (
            float(_median_decimal(other_gpu_values))
            if other_gpu_values
            else None
        ),
        "other_memory_percent_median": (
            float(_median_decimal(other_memory_values))
            if other_memory_values
            else None
        ),
    }
    if total_coverage < CONTENTION_MINIMUM_COVERAGE_SECONDS:
        return {
            **common,
            "status": "insufficient",
            "reason": "fewer than ten recent host minutes are available",
        }
    if attributed_coverage < total_coverage * Decimal("0.8"):
        return {
            **common,
            "status": "ambiguous",
            "reason": "recent CPU, GPU, or memory attribution is incomplete",
        }
    if (
        external_coverage >= Decimal("300")
        or external_coverage >= attributed_coverage * Decimal("0.5")
    ):
        return {
            **common,
            "status": "external_contention",
            "external_coverage_seconds": float(external_coverage),
            "signals": sorted(reasons),
            "reason": "sustained load outside BlueStacks is present",
        }
    return {
        **common,
        "status": "clear",
        "external_coverage_seconds": float(external_coverage),
        "signals": [],
        "reason": "no sustained external host contention is evident",
    }


def _recent_handle_samples(
    aggregates: Sequence[Mapping[str, Any]],
    *,
    since: datetime,
) -> list[tuple[datetime, Decimal, Decimal, bool, Decimal]]:
    result: list[tuple[datetime, Decimal, Decimal, bool, Decimal]] = []
    for aggregate in aggregates:
        metrics = aggregate.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        try:
            ended = _utc_timestamp(aggregate.get("window_end_utc"))
        except (TypeError, ValueError):
            continue
        if ended < since:
            continue
        handles = _decimal(metrics.get("bluestacks_handle_count_avg"))
        process_min = _decimal(metrics.get("bluestacks_process_count_min"))
        process_max = _decimal(metrics.get("bluestacks_process_count_max"))
        count = _decimal(aggregate.get("sample_count"))
        interval = _decimal(aggregate.get("sample_interval_ms"))
        if None in {handles, process_min, process_max, count, interval}:
            continue
        assert handles is not None
        assert process_min is not None
        assert process_max is not None
        assert count is not None
        assert interval is not None
        coverage = count * interval / Decimal(1000)
        if process_min <= 0 or process_max <= 0 or coverage <= 0:
            continue
        result.append(
            (
                ended,
                handles,
                coverage,
                process_min == process_max,
                process_min,
            )
        )
    return result


def _performance_intervals(values: object) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        return []
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        interval = value.get("interval")
        if not isinstance(interval, Mapping):
            continue
        captured_raw = value.get("captured_at")
        wave = value.get("saved_wave")
        cph = _decimal(interval.get("coins_per_hour"))
        speed = _decimal(interval.get("effective_game_speed"))
        elapsed = _decimal(interval.get("real_time_seconds"))
        try:
            captured = _utc_timestamp(captured_raw)
        except (TypeError, ValueError):
            continue
        if (
            type(wave) is not int
            or wave < 0
            or cph is None
            or cph <= 0
            or speed is None
            or speed <= 0
            or elapsed is None
            or not Decimal(120) <= elapsed <= Decimal(900)
        ):
            continue
        result.append(
            {
                "captured_at": captured,
                "wave": wave,
                "coins_per_hour": cph,
                "effective_game_speed": speed,
            }
        )
    return result


def _lower_envelope(values: Iterable[object]) -> Optional[Decimal]:
    normalized = sorted(
        value
        for item in values
        for value in (_decimal(item),)
        if value is not None and value > 0
    )
    if not normalized:
        return None
    return normalized[(len(normalized) - 1) // 5]


def _active_run_interval_history(record: Mapping[str, Any]) -> dict[str, Any]:
    runtime = record.get("runtime")
    evidence = (
        runtime.get("active_run_metrics")
        if isinstance(runtime, Mapping)
        else None
    )
    components = evidence.get("components") if isinstance(evidence, Mapping) else None
    economy = components.get("economy") if isinstance(components, Mapping) else None
    samples = economy.get("samples") if isinstance(economy, Mapping) else None
    return {
        "metric_mapping_id": (
            str(evidence.get("mapping_id") or "")
            if isinstance(evidence, Mapping)
            else ""
        ),
        "metric_semantic_fingerprint": (
            str(evidence.get("semantic_fingerprint") or "")
            if isinstance(evidence, Mapping)
            else ""
        ),
        "metric_intervals": [
            dict(sample)
            for sample in (samples or ())
            if isinstance(sample, Mapping)
            and isinstance(sample.get("interval"), Mapping)
        ],
    }


def _host_evidence(
    aggregates: Sequence[Mapping[str, Any]],
    *,
    when: datetime,
    lifetime_handle_summary: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    listener_identities = [
        _listener_identity(aggregate) for aggregate in aggregates
    ]
    if not aggregates or any(
        identity is None for identity in listener_identities
    ):
        return {
            "status": "unavailable",
            "identity_scope": "unavailable",
            "sample_count": 0,
            "sampler_session_count": 0,
            "reason": (
                "exact BlueStacks listener identity is unavailable; trend "
                "continuity cannot cross this GUI sampling boundary"
            ),
        }
    identities = {
        tuple(identity.items())
        for identity in listener_identities
        if identity is not None
    }
    if len(identities) != 1:
        return {
            "status": "identity_changed",
            "identity_scope": "exact_listener_lifetime",
            "sample_count": 0,
            "sampler_session_count": len(
                {
                    str(aggregate.get("session_id") or "")
                    for aggregate in aggregates
                    if aggregate.get("session_id")
                }
            ),
            "reason": (
                "the BlueStacks listener identity changed inside the "
                "assessment window"
            ),
        }
    listener_identity = next(
        identity for identity in listener_identities if identity is not None
    )
    common = {
        "identity_scope": "exact_listener_lifetime",
        "listener_identity": listener_identity,
        "sampler_session_count": max(
            len(
                {
                    str(aggregate.get("session_id") or "")
                    for aggregate in aggregates
                    if aggregate.get("session_id")
                }
            ),
            int(
                lifetime_handle_summary.get("sampler_session_count") or 0
            )
            if isinstance(lifetime_handle_summary, Mapping)
            and lifetime_handle_summary.get("listener_identity")
            == listener_identity
            else 0,
        ),
    }
    samples: list[
        tuple[
            datetime,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
        ]
    ] = []
    for aggregate in aggregates:
        metrics = aggregate.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        try:
            ended = _utc_timestamp(aggregate.get("window_end_utc"))
        except (TypeError, ValueError):
            continue
        handles = _decimal(metrics.get("bluestacks_handle_count_avg"))
        process_min = _decimal(metrics.get("bluestacks_process_count_min"))
        process_max = _decimal(metrics.get("bluestacks_process_count_max"))
        host_cpu = _decimal(metrics.get("host_cpu_percent_max"))
        host_memory = _decimal(metrics.get("host_memory_used_percent_max"))
        aggregate_sample_count = _decimal(aggregate.get("sample_count"))
        sample_interval_ms = _decimal(aggregate.get("sample_interval_ms"))
        if None in {
            handles,
            process_min,
            process_max,
            host_cpu,
            host_memory,
            aggregate_sample_count,
            sample_interval_ms,
        }:
            continue
        assert handles is not None
        assert process_min is not None
        assert process_max is not None
        assert host_cpu is not None
        assert host_memory is not None
        assert aggregate_sample_count is not None
        assert sample_interval_ms is not None
        if process_min <= 0 or process_max <= 0:
            continue
        sampled_seconds = (
            aggregate_sample_count * sample_interval_ms / Decimal(1000)
        )
        if sampled_seconds <= 0:
            continue
        samples.append(
            (
                ended,
                handles,
                process_min,
                process_max,
                host_cpu,
                host_memory,
                sampled_seconds,
            )
        )
    samples.sort(key=lambda item: item[0])
    lifetime_low_water: Optional[Decimal] = None
    if samples:
        observed_recent = [
            sample
            for sample in samples
            if sample[0] >= when - RECENT_HOST_WINDOW
        ]
        observed_process_counts = {
            sample[2]
            for sample in observed_recent
            if sample[2] == sample[3]
        }
        if (
            isinstance(lifetime_handle_summary, Mapping)
            and lifetime_handle_summary.get("listener_identity")
            == listener_identity
        ):
            by_process_count = lifetime_handle_summary.get(
                "handle_low_water_by_process_count"
            )
            if (
                isinstance(by_process_count, Mapping)
                and len(observed_process_counts) == 1
            ):
                lifetime_low_water = _decimal(
                    by_process_count.get(
                        str(int(next(iter(observed_process_counts))))
                    )
                )
            if lifetime_low_water is None:
                lifetime_low_water = _decimal(
                    lifetime_handle_summary.get("handle_low_water")
                )
        observed_low_water = lifetime_low_water or min(
            sample[1] for sample in samples
        )
        observed_current = _median_decimal(
            sample[1] for sample in observed_recent
        )
        if observed_current is not None:
            observed_ratio = (
                observed_current / observed_low_water
                if observed_low_water > 0
                else Decimal(0)
            )
            common.update(
                {
                    "handle_low_water": float(observed_low_water),
                    "handle_recent_median": float(observed_current),
                    "handle_ratio": float(observed_ratio),
                    "handle_delta": float(
                        observed_current - observed_low_water
                    ),
                }
            )
    if len(samples) < MINIMUM_HOST_WINDOWS:
        return {
            **common,
            "status": "insufficient",
            "sample_count": len(samples),
            "reason": "fewer than 16 minutes of stable host windows are available",
        }
    span = samples[-1][0] - samples[0][0]
    if span < MINIMUM_HOST_SPAN:
        return {
            **common,
            "status": "insufficient",
            "sample_count": len(samples),
            "span_seconds": int(span.total_seconds()),
            "reason": "stable host coverage is too short",
        }
    sampled_coverage = sum((sample[6] for sample in samples), Decimal(0))
    if sampled_coverage < MINIMUM_HOST_COVERAGE_SECONDS:
        return {
            **common,
            "status": "insufficient",
            "sample_count": len(samples),
            "span_seconds": int(span.total_seconds()),
            "sampled_coverage_seconds": float(sampled_coverage),
            "reason": "fewer than 16 sampled host minutes are available",
        }
    recent = [
        sample
        for sample in samples
        if sample[0] >= when - RECENT_HOST_WINDOW
    ]
    if len(recent) < MINIMUM_HOST_WINDOWS:
        return {
            **common,
            "status": "insufficient",
            "sample_count": len(recent),
            "reason": "the recent sustained host window is incomplete",
        }
    recent_coverage = sum((sample[6] for sample in recent), Decimal(0))
    if recent_coverage < MINIMUM_HOST_COVERAGE_SECONDS:
        return {
            **common,
            "status": "insufficient",
            "sample_count": len(recent),
            "sampled_coverage_seconds": float(recent_coverage),
            "reason": "the recent sampled host coverage is incomplete",
        }
    if any(sample[4] >= 95 or sample[5] >= 95 for sample in recent):
        return {
            **common,
            "status": "saturated",
            "sample_count": len(recent),
            "reason": "recent host CPU or memory reached 95 percent",
        }
    stable_windows = sum(sample[2] == sample[3] for sample in recent)
    stable_coverage = sum(
        (sample[6] for sample in recent if sample[2] == sample[3]),
        Decimal(0),
    )
    stable_process_counts = {
        sample[2] for sample in recent if sample[2] == sample[3]
    }
    if (
        stable_coverage < recent_coverage * Decimal("0.9")
        or len(stable_process_counts) != 1
    ):
        return {
            **common,
            "status": "unstable_process_set",
            "sample_count": len(recent),
            "stable_process_windows": stable_windows,
            "sampled_coverage_seconds": float(recent_coverage),
            "reason": "the recent BlueStacks process set was not stable",
        }
    low_water = lifetime_low_water or min(sample[1] for sample in samples)
    current = _median_decimal(sample[1] for sample in recent)
    assert current is not None
    ratio = current / low_water if low_water > 0 else Decimal(0)
    delta = current - low_water
    confirmed = ratio >= MINIMUM_HANDLE_RATIO and delta >= MINIMUM_HANDLE_DELTA
    return {
        **common,
        "status": "confirmed_growth" if confirmed else "stable",
        "sample_count": len(recent),
        "span_seconds": int(span.total_seconds()),
        "stable_process_windows": stable_windows,
        "sampled_coverage_seconds": float(recent_coverage),
        "handle_low_water": float(low_water),
        "handle_recent_median": float(current),
        "handle_ratio": float(ratio),
        "handle_delta": float(delta),
        "reason": (
            "sustained handle growth meets the ratio and absolute thresholds"
            if confirmed
            else "BlueStacks handle growth remains below the restart threshold"
        ),
    }


def _listener_identity(
    aggregate: Mapping[str, Any],
) -> Optional[dict[str, object]]:
    listener = aggregate.get("bluestacks_listener")
    if not isinstance(listener, Mapping):
        return None
    keys = (
        "host_id",
        "adb_port",
        "process_id",
        "process_started_at",
        "executable_path",
        "instance_name",
    )
    if any(listener.get(key) in {None, ""} for key in keys):
        return None
    return {key: listener.get(key) for key in keys}


def _report_decimal(record: Mapping[str, Any], key: str) -> Optional[Decimal]:
    pending: list[object] = [
        record.get("more_stats"),
        record.get("detailed_stats"),
        record.get("game_stats"),
    ]
    visited = 0
    while pending and visited < 20_000:
        visited += 1
        current = pending.pop()
        if isinstance(current, Mapping):
            if current.get("key") == key:
                value = _decimal(
                    current.get("value_decimal", current.get("value"))
                )
                if value is not None:
                    return value
                raw = current.get("value_raw", current.get("raw"))
                if isinstance(raw, str):
                    parsed = parse_tower_number(raw)
                    if parsed is not None:
                        return parsed
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def _decimal_at(
    value: Mapping[str, Any],
    first: str,
    second: str,
) -> Optional[Decimal]:
    nested = value.get(first)
    return _decimal(nested.get(second)) if isinstance(nested, Mapping) else None


def _decimal(value: object) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _median_decimal(values: Iterable[object]) -> Optional[Decimal]:
    normalized = [value for item in values if (value := _decimal(item)) is not None]
    return median(normalized) if normalized else None


def _fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _record_timestamp(record: Mapping[str, object]) -> float:
    try:
        return _utc_timestamp(record.get("captured_at")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _utc_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value or ""))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "AUTOMATIC_RESTART_COOLDOWN",
    "assess_emulator_degradation",
    "load_comparable_battles",
]
