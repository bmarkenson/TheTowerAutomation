"""Observation-backed planning for Elite Cell Lab Speedup spending.

The planner deliberately has no game-input authority.  It validates an
operator-authored normal and reserve target for each of the five Labs, applies
the Cell prices observed in the game UI, and compares those costs with a
duration-weighted average from completed battle records.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Iterable, Mapping, Optional, Sequence

CELL_BALANCE_POLICY_SCHEMA_VERSION = 1
LAB_SPEED_PLAN_SCHEMA_VERSION = 1
LAB_COUNT = 5
HISTORICAL_INCOME_MAX_BATTLES = 20
LAB_SPEED_COST_SOURCE = "live_lab_speedup_ui_game_1101_2026-08-20"
LAB_SPEED_COSTS_PER_HOUR: dict[str, Decimal] = {
    "1": Decimal(0),
    "1.5": Decimal(15),
    "2": Decimal(100),
    "3": Decimal(840),
    "4": Decimal(3360),
    "5": Decimal(11900),
    "6": Decimal(60000),
    "7": Decimal(250000),
    "8": Decimal(1000000),
}
VALID_LAB_SPEEDS = tuple(LAB_SPEED_COSTS_PER_HOUR)
_MAX_CELL_INTEGER_DIGITS = 36


def empty_cell_balance_policy() -> dict[str, Any]:
    """Return the safe UI default when no policy has been saved yet."""

    return {
        "schema_version": CELL_BALANCE_POLICY_SCHEMA_VERSION,
        "buffer_floor_decimal": None,
        "labs": [
            {
                "lab": lab,
                "normal_speed": None,
                "reserve_speed": None,
            }
            for lab in range(1, LAB_COUNT + 1)
        ],
        "automatic_reduction_enabled": False,
        "updated_at": None,
        "updated_by": None,
        "request_id": None,
    }


def normalize_cell_balance_policy(value: object) -> Optional[dict[str, Any]]:
    """Validate one complete persisted reserve and per-Lab target policy."""

    if not isinstance(value, Mapping):
        return None
    if (
        value.get("schema_version") != CELL_BALANCE_POLICY_SCHEMA_VERSION
        or value.get("automatic_reduction_enabled") is not False
    ):
        return None
    try:
        floor = _whole_decimal_text(
            value.get("buffer_floor_decimal"),
            optional=True,
        )
    except ValueError:
        return None
    raw_labs = value.get("labs")
    if not isinstance(raw_labs, list) or len(raw_labs) != LAB_COUNT:
        return None
    labs: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_lab in raw_labs:
        if not isinstance(raw_lab, Mapping):
            return None
        lab = raw_lab.get("lab")
        if type(lab) is not int or not 1 <= lab <= LAB_COUNT or lab in seen:
            return None
        raw_normal = raw_lab.get("normal_speed")
        raw_reserve = raw_lab.get("reserve_speed")
        normal = _normalize_speed(raw_normal, optional=True)
        reserve = _normalize_speed(raw_reserve, optional=True)
        if (raw_normal is not None and normal is None) or (
            raw_reserve is not None and reserve is None
        ):
            return None
        if (normal is None) != (reserve is None):
            return None
        if normal is not None and Decimal(reserve) > Decimal(normal):
            return None
        seen.add(lab)
        labs.append(
            {
                "lab": lab,
                "normal_speed": normal,
                "reserve_speed": reserve,
            }
        )
    if seen != set(range(1, LAB_COUNT + 1)):
        return None

    updated_at = _bounded_optional_text(value.get("updated_at"), 64)
    updated_by = _bounded_optional_text(value.get("updated_by"), 128)
    request_id = _bounded_optional_text(value.get("request_id"), 128)
    if value.get("updated_at") is not None and updated_at is None:
        return None
    if value.get("updated_by") is not None and updated_by is None:
        return None
    if value.get("request_id") is not None and request_id is None:
        return None
    return {
        "schema_version": CELL_BALANCE_POLICY_SCHEMA_VERSION,
        "buffer_floor_decimal": floor,
        "labs": sorted(labs, key=lambda item: item["lab"]),
        "automatic_reduction_enabled": False,
        "updated_at": updated_at,
        "updated_by": updated_by,
        "request_id": request_id,
    }


def new_cell_balance_policy(
    *,
    buffer_floor_decimal: object,
    labs: object,
    updated_at: str,
    updated_by: str,
    request_id: str,
) -> dict[str, Any]:
    """Build an operator policy, allowing incomplete planner selections."""

    try:
        floor = _whole_decimal_text(buffer_floor_decimal, optional=True)
    except ValueError as exc:
        raise ValueError(
            "Cell reserve must be a nonnegative whole number or null"
        ) from exc
    if not isinstance(labs, list) or len(labs) != LAB_COUNT:
        raise ValueError("Exactly five Lab speed selections are required")
    candidate_labs: list[dict[str, Any]] = []
    for item in labs:
        if not isinstance(item, Mapping):
            raise ValueError("Each Lab speed selection must be an object")
        lab = item.get("lab")
        raw_normal = item.get("normal_speed")
        raw_reserve = item.get("reserve_speed")
        normal = _normalize_speed(raw_normal, optional=True)
        reserve = _normalize_speed(raw_reserve, optional=True)
        if (raw_normal is not None and normal is None) or (
            raw_reserve is not None and reserve is None
        ):
            raise ValueError("Lab speeds must use one of the supported multipliers")
        if (normal is None) != (reserve is None):
            raise ValueError(
                "Each Lab requires both normal and reserve speeds or neither"
            )
        candidate_labs.append(
            {
                "lab": lab,
                "normal_speed": normal,
                "reserve_speed": reserve,
            }
        )
    candidate = {
        "schema_version": CELL_BALANCE_POLICY_SCHEMA_VERSION,
        "buffer_floor_decimal": floor,
        "labs": candidate_labs,
        "automatic_reduction_enabled": False,
        "updated_at": updated_at,
        "updated_by": updated_by,
        "request_id": request_id,
    }
    normalized = normalize_cell_balance_policy(candidate)
    if normalized is None:
        raise ValueError(
            "Labs must be numbered 1 through 5; each configured reserve speed "
            "must be no higher than its normal speed"
        )
    return normalized


def historical_cell_income(
    records: Iterable[Mapping[str, Any]],
    *,
    max_battles: int = HISTORICAL_INCOME_MAX_BATTLES,
) -> dict[str, Any]:
    """Return a duration-weighted gross Cell rate from completed battles."""

    if type(max_battles) is not int or max_battles < 1:
        raise ValueError("Historical battle limit must be positive")
    # Keep the foundational control-directive import path light. Completed
    # battle parsing is needed only by the HTTP planner status.
    from core.battle_stats import included_in_default_history

    candidates: list[tuple[datetime, Decimal, Decimal]] = []
    excluded = 0
    for record in records:
        if not isinstance(record, Mapping) or not included_in_default_history(record):
            excluded += 1
            continue
        quality = record.get("quality")
        if isinstance(quality, Mapping) and quality.get("valid") is False:
            excluded += 1
            continue
        captured = _timestamp(record.get("captured_at"))
        cells = _battle_row_decimal(record, "cells_earned")
        real_seconds = _battle_duration_seconds(record, "real_time")
        if (
            captured is None
            or cells is None
            or cells < 0
            or real_seconds is None
            or real_seconds <= 0
        ):
            excluded += 1
            continue
        candidates.append((captured, cells, real_seconds))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[:max_battles]
    if not selected:
        return {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "completed_battle_cell_history_unavailable",
            "basis": "duration_weighted_completed_battles",
            "sample_count": 0,
            "max_battles": max_battles,
            "excluded_count": excluded,
            "total_cells_decimal": None,
            "total_real_hours_decimal": None,
            "cells_per_hour_decimal": None,
            "oldest_captured_at": None,
            "newest_captured_at": None,
        }
    total_cells = sum((item[1] for item in selected), Decimal(0))
    total_seconds = sum((item[2] for item in selected), Decimal(0))
    with localcontext() as context:
        context.prec = 50
        total_hours = total_seconds / Decimal(3600)
        cells_per_hour = total_cells / total_hours
    return {
        "schema_version": 1,
        "status": "observed",
        "reason": "",
        "basis": "duration_weighted_completed_battles",
        "sample_count": len(selected),
        "max_battles": max_battles,
        "excluded_count": excluded,
        "total_cells_decimal": _decimal_text(total_cells),
        "total_real_hours_decimal": _decimal_text(total_hours),
        "cells_per_hour_decimal": _decimal_text(cells_per_hour),
        "oldest_captured_at": selected[-1][0].isoformat(),
        "newest_captured_at": selected[0][0].isoformat(),
    }


def build_lab_speed_plan_status(
    policy: object,
    *,
    historical_income: Mapping[str, Any],
    cell_balance: object = None,
    active_run_metrics: object = None,
    policy_error: Optional[str] = None,
) -> dict[str, Any]:
    """Combine policy, exact prices, history, and current observation."""

    normalized = normalize_cell_balance_policy(policy)
    if normalized is None:
        normalized = empty_cell_balance_policy()
    labs = []
    complete = True
    for item in normalized["labs"]:
        normal_speed = item["normal_speed"]
        reserve_speed = item["reserve_speed"]
        if normal_speed is None or reserve_speed is None:
            complete = False
        labs.append(
            {
                **item,
                "normal_cells_per_hour_decimal": (
                    _decimal_text(LAB_SPEED_COSTS_PER_HOUR[normal_speed])
                    if normal_speed is not None
                    else None
                ),
                "reserve_cells_per_hour_decimal": (
                    _decimal_text(LAB_SPEED_COSTS_PER_HOUR[reserve_speed])
                    if reserve_speed is not None
                    else None
                ),
                "savings_per_hour_decimal": (
                    _decimal_text(
                        LAB_SPEED_COSTS_PER_HOUR[normal_speed]
                        - LAB_SPEED_COSTS_PER_HOUR[reserve_speed]
                    )
                    if normal_speed is not None and reserve_speed is not None
                    else None
                ),
            }
        )

    income_rate = _mapping_decimal(
        historical_income,
        "cells_per_hour_decimal",
        nonnegative=True,
    )
    history_available = bool(
        historical_income.get("status") == "observed"
        and income_rate is not None
    )
    balance_value, actual_net = _balance_values(cell_balance)
    floor = _optional_decimal(normalized["buffer_floor_decimal"])
    normal = _plan_projection(
        labs,
        cost_field="normal_cells_per_hour_decimal",
        income_rate=income_rate if history_available else None,
        balance=balance_value,
        floor=floor,
    )
    reserve = _plan_projection(
        labs,
        cost_field="reserve_cells_per_hour_decimal",
        income_rate=income_rate if history_available else None,
        balance=balance_value,
        floor=floor,
    )
    if not complete:
        recommendation = {
            "status": "policy_incomplete",
            "reason": "Set normal and reserve speeds for all five Labs",
        }
    elif not history_available:
        recommendation = {
            "status": "income_history_unavailable",
            "reason": "No usable completed-battle Cell history is available",
        }
    else:
        normal_net = _optional_decimal(normal["projected_net_per_hour_decimal"])
        reserve_net = _optional_decimal(reserve["projected_net_per_hour_decimal"])
        reserve_breached = bool(
            balance_value is not None
            and floor is not None
            and balance_value <= floor
        )
        if reserve_net is not None and reserve_net < 0:
            status = "reserve_plan_still_declines"
            reason = "Historical gross Cell income does not cover the reserve plan"
        elif reserve_breached:
            status = "reserve_floor_breached"
            reason = (
                "The observed Cell balance is at or below the saved reserve; "
                "review the reserve targets before the next renewals"
            )
        elif (
            normal_net is not None
            and normal_net >= 0
            and actual_net is not None
            and actual_net < 0
        ):
            status = "observed_decline_despite_forecast"
            reason = (
                "Historical income covers the normal plan, but the observed "
                "Cell balance is currently falling"
            )
        elif actual_net is not None and actual_net < 0:
            status = "observed_decline_reserve_plan_recovers"
            reason = (
                "The observed Cell balance is falling; the reserve plan is "
                "projected to make Cell flow nonnegative"
            )
        elif normal_net is not None and normal_net >= 0:
            status = "normal_plan_sustainable"
            reason = "Historical gross Cell income covers the normal plan"
        elif reserve_net is not None and reserve_net >= 0:
            status = "reserve_plan_recovers"
            reason = "The reserve plan changes projected Cell flow to nonnegative"
        else:
            status = "reserve_plan_still_declines"
            reason = "Historical gross Cell income does not cover the reserve plan"
        recommendation = {"status": status, "reason": reason}

    return {
        "schema_version": LAB_SPEED_PLAN_SCHEMA_VERSION,
        "status": (
            "invalid_policy"
            if policy_error
            else "ready"
            if complete
            else "incomplete"
        ),
        "reason": policy_error or ("" if complete else "lab_speed_policy_incomplete"),
        "policy": {**normalized, "labs": labs},
        "cost_model": {
            "source": LAB_SPEED_COST_SOURCE,
            "duration_scaling": "linear",
            "cells_per_hour_by_speed": {
                speed: _decimal_text(cost)
                for speed, cost in LAB_SPEED_COSTS_PER_HOUR.items()
            },
        },
        "income": dict(historical_income),
        "current_run_cells_per_hour_decimal": _current_run_rate(
            active_run_metrics
        ),
        "actual_balance_net_per_hour_decimal": (
            _signed_decimal_text(actual_net) if actual_net is not None else None
        ),
        "normal_plan": normal,
        "reserve_plan": reserve,
        "recommendation": recommendation,
        "application_boundary": "next_queue_or_renewal",
        "automatic_application_enabled": False,
        "ui_action_authority": False,
    }


def _plan_projection(
    labs: Sequence[Mapping[str, Any]],
    *,
    cost_field: str,
    income_rate: Optional[Decimal],
    balance: Optional[Decimal],
    floor: Optional[Decimal],
) -> dict[str, Any]:
    costs = [_optional_decimal(item.get(cost_field)) for item in labs]
    if any(cost is None for cost in costs):
        return {
            "complete": False,
            "burn_per_hour_decimal": None,
            "burn_per_day_decimal": None,
            "projected_net_per_hour_decimal": None,
            "projected_net_per_day_decimal": None,
            "estimated_hours_to_floor_decimal": None,
        }
    burn = sum((cost for cost in costs if cost is not None), Decimal(0))
    net = income_rate - burn if income_rate is not None else None
    estimate = None
    if (
        net is not None
        and net < 0
        and balance is not None
        and floor is not None
        and balance > floor
    ):
        with localcontext() as context:
            context.prec = 50
            estimate = (balance - floor) / -net
    return {
        "complete": True,
        "burn_per_hour_decimal": _decimal_text(burn),
        "burn_per_day_decimal": _decimal_text(burn * Decimal(24)),
        "projected_net_per_hour_decimal": (
            _signed_decimal_text(net) if net is not None else None
        ),
        "projected_net_per_day_decimal": (
            _signed_decimal_text(net * Decimal(24)) if net is not None else None
        ),
        "estimated_hours_to_floor_decimal": (
            _decimal_text(estimate) if estimate is not None else None
        ),
    }


def _balance_values(value: object) -> tuple[Optional[Decimal], Optional[Decimal]]:
    if not isinstance(value, Mapping) or value.get("status") != "observed":
        return None, None
    balance = _mapping_decimal(value, "balance_decimal", nonnegative=True)
    trend = value.get("trend")
    net = (
        _mapping_decimal(trend, "net_per_hour_decimal", nonnegative=False)
        if isinstance(trend, Mapping)
        else None
    )
    return balance, net


def _current_run_rate(value: object) -> Optional[str]:
    if not isinstance(value, Mapping):
        return None
    whole_run = value.get("whole_run")
    rate = (
        _mapping_decimal(whole_run, "cells_per_hour", nonnegative=True)
        if isinstance(whole_run, Mapping)
        else None
    )
    return _decimal_text(rate) if rate is not None else None


def _battle_row(record: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    stats = record.get("more_stats") or record.get("detailed_stats")
    sections = stats.get("sections") if isinstance(stats, Mapping) else None
    if not isinstance(sections, list):
        return None
    for section in sections:
        if not isinstance(section, Mapping) or section.get("key") != "battle_report":
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, Mapping) and row.get("key") == key:
                return row
    return None


def _battle_row_decimal(
    record: Mapping[str, Any],
    key: str,
) -> Optional[Decimal]:
    row = _battle_row(record, key)
    if row is None:
        return None
    for field in ("value_decimal", "value"):
        value = _optional_decimal(row.get(field))
        if value is not None:
            return value
    from core.battle_stats import parse_tower_number

    return parse_tower_number(str(row.get("value_raw") or ""))


def _battle_duration_seconds(
    record: Mapping[str, Any],
    key: str,
) -> Optional[Decimal]:
    row = _battle_row(record, key)
    if row is None:
        return None
    for field in ("value_decimal", "value"):
        value = _optional_decimal(row.get(field))
        if value is not None:
            return value
    from core.battle_stats import parse_duration_seconds

    parsed = parse_duration_seconds(str(row.get("value_raw") or ""))
    return Decimal(parsed) if parsed is not None else None


def _normalize_speed(value: object, *, optional: bool) -> Optional[str]:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        return None
    try:
        decimal = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    for speed in VALID_LAB_SPEEDS:
        if decimal == Decimal(speed):
            return speed
    return None


def _whole_decimal_text(value: object, *, optional: bool) -> Optional[str]:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a Cell total")
    text = str("" if value is None else value).strip()
    if (
        not text
        or len(text) > _MAX_CELL_INTEGER_DIGITS
        or not text.isascii()
        or not text.isdigit()
    ):
        raise ValueError("Cell total must contain decimal digits only")
    return str(int(text))


def _mapping_decimal(
    value: Mapping[str, Any],
    field: str,
    *,
    nonnegative: bool,
) -> Optional[Decimal]:
    decimal = _optional_decimal(value.get(field))
    if decimal is None or (nonnegative and decimal < 0):
        return None
    return decimal


def _optional_decimal(value: object) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return decimal if decimal.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed_decimal_text(value: Decimal) -> str:
    text = _decimal_text(value)
    return f"+{text}" if value > 0 else text


def _bounded_optional_text(value: object, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and len(text) <= limit else None


def _timestamp(value: object) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


__all__ = [
    "CELL_BALANCE_POLICY_SCHEMA_VERSION",
    "HISTORICAL_INCOME_MAX_BATTLES",
    "LAB_SPEED_COSTS_PER_HOUR",
    "LAB_SPEED_PLAN_SCHEMA_VERSION",
    "VALID_LAB_SPEEDS",
    "build_lab_speed_plan_status",
    "empty_cell_balance_policy",
    "historical_cell_income",
    "new_cell_balance_policy",
    "normalize_cell_balance_policy",
]
