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
RECENT_HOST_WINDOW = timedelta(minutes=20)
MINIMUM_HANDLE_RATIO = Decimal("1.8")
MINIMUM_HANDLE_DELTA = Decimal("4000")
AUTOMATIC_RESTART_COOLDOWN = timedelta(hours=8)


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
    assessed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assess two comparable runs plus same-session sustained handle growth."""

    when = (assessed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = {
        "schema_version": DEGRADATION_SCHEMA_VERSION,
        "assessed_at": when.isoformat(timespec="seconds"),
        "current_run_id": current_run_id,
        "current_strategy": str(current_strategy or "").strip().lower() or None,
        "automatic_ready": False,
        "candidate_battle_ids": [],
        "baseline_battle_ids": [],
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

    host = _host_evidence(host_aggregates, when=when)
    evidence["host_evidence"] = host
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
                "two comparable Farm runs are degraded, but sustained same-session "
                "host corroboration is incomplete"
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


def _host_evidence(
    aggregates: Sequence[Mapping[str, Any]],
    *,
    when: datetime,
) -> dict[str, Any]:
    samples: list[
        tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal]
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
        if None in {
            handles,
            process_min,
            process_max,
            host_cpu,
            host_memory,
        }:
            continue
        assert handles is not None
        assert process_min is not None
        assert process_max is not None
        assert host_cpu is not None
        assert host_memory is not None
        if process_min <= 0 or process_max <= 0:
            continue
        samples.append(
            (
                ended,
                handles,
                process_min,
                process_max,
                host_cpu,
                host_memory,
            )
        )
    if len(samples) < MINIMUM_HOST_WINDOWS:
        return {
            "status": "insufficient",
            "sample_count": len(samples),
            "reason": "fewer than 16 minutes of stable host windows are available",
        }
    samples.sort(key=lambda item: item[0])
    span = samples[-1][0] - samples[0][0]
    if span < MINIMUM_HOST_SPAN:
        return {
            "status": "insufficient",
            "sample_count": len(samples),
            "span_seconds": int(span.total_seconds()),
            "reason": "stable host coverage is too short",
        }
    recent = [
        sample
        for sample in samples
        if sample[0] >= when - RECENT_HOST_WINDOW
    ]
    if len(recent) < MINIMUM_HOST_WINDOWS:
        return {
            "status": "insufficient",
            "sample_count": len(recent),
            "reason": "the recent sustained host window is incomplete",
        }
    if any(sample[4] >= 95 or sample[5] >= 95 for sample in recent):
        return {
            "status": "saturated",
            "sample_count": len(recent),
            "reason": "recent host CPU or memory reached 95 percent",
        }
    stable_windows = sum(sample[2] == sample[3] for sample in recent)
    if stable_windows < int(len(recent) * 0.9):
        return {
            "status": "unstable_process_set",
            "sample_count": len(recent),
            "stable_process_windows": stable_windows,
            "reason": "the recent BlueStacks process set was not stable",
        }
    low_water = min(sample[1] for sample in samples)
    current = _median_decimal(sample[1] for sample in recent)
    assert current is not None
    ratio = current / low_water if low_water > 0 else Decimal(0)
    delta = current - low_water
    confirmed = ratio >= MINIMUM_HANDLE_RATIO and delta >= MINIMUM_HANDLE_DELTA
    return {
        "status": "confirmed_growth" if confirmed else "stable",
        "sample_count": len(recent),
        "span_seconds": int(span.total_seconds()),
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
