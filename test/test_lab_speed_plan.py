from datetime import datetime, timedelta, timezone

import pytest

from core.lab_speed_plan import (
    build_lab_speed_plan_status,
    empty_cell_balance_policy,
    historical_cell_income,
    new_cell_balance_policy,
    normalize_cell_balance_policy,
)


START = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _policy(*, normal=("6", "6", "6", "6", "5"), reserve=("5",) * 5):
    return new_cell_balance_policy(
        buffer_floor_decimal="10000000",
        labs=[
            {
                "lab": index,
                "normal_speed": normal[index - 1],
                "reserve_speed": reserve[index - 1],
            }
            for index in range(1, 6)
        ],
        updated_at=START.isoformat(),
        updated_by="test",
        request_id="policy-1",
    )


def _battle(*, captured_at, cells, real_seconds):
    return {
        "battle_id": "Battle" + captured_at.strftime("%Y%m%dT%H%M%S%z"),
        "captured_at": captured_at.isoformat(),
        "quality": {"valid": True},
        "more_stats": {
            "sections": [
                {
                    "key": "battle_report",
                    "rows": [
                        {
                            "key": "cells_earned",
                            "value_decimal": str(cells),
                            "value_raw": str(cells),
                        },
                        {
                            "key": "real_time",
                            "value_decimal": str(real_seconds),
                            "value_raw": f"{real_seconds}s",
                        },
                    ],
                }
            ]
        },
    }


def test_policy_requires_five_ordered_labs_and_nonincreasing_reserve():
    policy = _policy()

    assert normalize_cell_balance_policy(policy) == policy
    assert policy["buffer_floor_decimal"] == "10000000"
    assert [item["normal_speed"] for item in policy["labs"]] == [
        "6",
        "6",
        "6",
        "6",
        "5",
    ]
    assert normalize_cell_balance_policy(
        {
            **policy,
            "labs": [
                *policy["labs"][:4],
                {"lab": 5, "normal_speed": "bogus", "reserve_speed": "bogus"},
            ],
        }
    ) is None
    with pytest.raises(ValueError, match="reserve speed"):
        _policy(normal=("5",) * 5, reserve=("6",) * 5)


def test_policy_can_save_buffer_before_lab_plan_is_complete():
    policy = new_cell_balance_policy(
        buffer_floor_decimal="12000000",
        labs=[
            {"lab": lab, "normal_speed": None, "reserve_speed": None}
            for lab in range(1, 6)
        ],
        updated_at=START.isoformat(),
        updated_by="test",
        request_id="buffer-only",
    )

    assert policy["buffer_floor_decimal"] == "12000000"
    assert all(item["normal_speed"] is None for item in policy["labs"])
    with pytest.raises(ValueError, match="both normal and reserve"):
        new_cell_balance_policy(
            buffer_floor_decimal="12000000",
            labs=[
                {
                    "lab": lab,
                    "normal_speed": "5" if lab == 1 else None,
                    "reserve_speed": None,
                }
                for lab in range(1, 6)
            ],
            updated_at=START.isoformat(),
            updated_by="test",
            request_id="partial-pair",
        )


def test_completed_battle_income_is_duration_weighted():
    income = historical_cell_income(
        [
            _battle(captured_at=START, cells=100, real_seconds=3600),
            _battle(
                captured_at=START - timedelta(days=1),
                cells=600,
                real_seconds=10800,
            ),
        ]
    )

    assert income["status"] == "observed"
    assert income["sample_count"] == 2
    assert income["total_cells_decimal"] == "700"
    assert income["total_real_hours_decimal"] == "4"
    assert income["cells_per_hour_decimal"] == "175"


def test_planner_applies_observed_cost_curve_and_separates_actual_net():
    status = build_lab_speed_plan_status(
        _policy(),
        historical_income={
            "schema_version": 1,
            "status": "observed",
            "reason": "",
            "basis": "duration_weighted_completed_battles",
            "sample_count": 8,
            "max_battles": 20,
            "excluded_count": 0,
            "total_cells_decimal": "4000000",
            "total_real_hours_decimal": "20",
            "cells_per_hour_decimal": "200000",
            "oldest_captured_at": (START - timedelta(days=8)).isoformat(),
            "newest_captured_at": START.isoformat(),
        },
        cell_balance={
            "status": "observed",
            "balance_decimal": "13000000",
            "trend": {"net_per_hour_decimal": "-15000"},
        },
        active_run_metrics={
            "whole_run": {"cells_per_hour": "182927.12"},
        },
    )

    assert status["normal_plan"]["burn_per_hour_decimal"] == "251900"
    assert status["normal_plan"]["burn_per_day_decimal"] == "6045600"
    assert status["normal_plan"]["projected_net_per_hour_decimal"] == "-51900"
    assert status["reserve_plan"]["burn_per_hour_decimal"] == "59500"
    assert status["reserve_plan"]["projected_net_per_hour_decimal"] == "+140500"
    assert status["actual_balance_net_per_hour_decimal"] == "-15000"
    assert status["current_run_cells_per_hour_decimal"] == "182927.12"
    assert (
        status["recommendation"]["status"]
        == "observed_decline_reserve_plan_recovers"
    )
    assert status["automatic_application_enabled"] is False
    assert status["ui_action_authority"] is False


def test_empty_policy_is_explicitly_incomplete():
    status = build_lab_speed_plan_status(
        empty_cell_balance_policy(),
        historical_income={
            "status": "unavailable",
            "cells_per_hour_decimal": None,
        },
    )

    assert status["status"] == "incomplete"
    assert status["recommendation"]["status"] == "policy_incomplete"
    assert status["normal_plan"]["complete"] is False


def test_observed_decline_is_not_hidden_by_a_positive_historical_forecast():
    status = build_lab_speed_plan_status(
        _policy(),
        historical_income={
            "status": "observed",
            "cells_per_hour_decimal": "338815",
        },
        cell_balance={
            "status": "observed",
            "balance_decimal": "13700000",
            "trend": {"net_per_hour_decimal": "-14686"},
        },
    )

    assert status["normal_plan"]["projected_net_per_hour_decimal"] == "+86915"
    assert status["recommendation"] == {
        "status": "observed_decline_despite_forecast",
        "reason": (
            "Historical income covers the normal plan, but the observed Cell "
            "balance is currently falling"
        ),
    }


def test_observed_decline_remains_a_warning_when_reserve_plan_recovers():
    status = build_lab_speed_plan_status(
        _policy(),
        historical_income={
            "status": "observed",
            "cells_per_hour_decimal": "200000",
        },
        cell_balance={
            "status": "observed",
            "balance_decimal": "13700000",
            "trend": {"net_per_hour_decimal": "-14686"},
        },
    )

    assert status["recommendation"] == {
        "status": "observed_decline_reserve_plan_recovers",
        "reason": (
            "The observed Cell balance is falling; the reserve plan is "
            "projected to make Cell flow nonnegative"
        ),
    }
