from decimal import Decimal
from unittest.mock import patch

import numpy as np

from core.automation_supervisor import AutomationSupervisor
from utils.coin_detector import format_compact_decimal, parse_compact_number


def test_compact_parser_preserves_magnitude_before_per_min_marker():
    assert parse_compact_number("37.19M/min") == Decimal("37.19e6")
    assert parse_compact_number("37.19q/min") == Decimal("37.19e15")


def test_compact_parser_supports_case_sensitive_larger_magnitudes():
    assert parse_compact_number("2.1q/min") == Decimal("2.1e15")
    assert parse_compact_number("2.1Q/min") == Decimal("2.1e18")
    assert parse_compact_number("12.3s/min") == Decimal("12.3e21")
    assert parse_compact_number("4.5S/min") == Decimal("4.5e24")
    assert parse_compact_number("1.2aa/min") == Decimal("1.2e36")
    assert format_compact_decimal(Decimal("1.2e36")) == "1.2aa"


def test_supervisor_recovers_missing_suffix_from_recent_rate(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    supervisor._last_coins_val = Decimal("36.7e15")

    with patch("core.automation_supervisor.log") as runtime_log:
        value, confidence, has_min, effective = supervisor.process_coins(
            np.zeros((1, 1, 3), dtype=np.uint8),
            Decimal("37.19"),
            75.0,
            True,
            allow_actions=False,
        )

    assert value == Decimal("37.19e15")
    assert confidence == 75.0
    assert has_min is True
    assert effective == Decimal("37.19e15")
    assert supervisor._last_coins_val == Decimal("37.19e15")
    assert any(
        call.args
        and "Recovered missing magnitude suffix 37.19 → 37.19q"
        in call.args[0]
        for call in runtime_log.call_args_list
    )


def test_supervisor_still_rejects_true_implausible_drop(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    supervisor._last_coins_val = Decimal("36.7e15")

    _, _, _, effective = supervisor.process_coins(
        np.zeros((1, 1, 3), dtype=np.uint8),
        Decimal("2e12"),
        75.0,
        True,
        allow_actions=False,
    )

    assert effective == Decimal("36.7e15")
