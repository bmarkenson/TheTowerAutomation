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


def test_supervisor_confirms_sustained_ramp_after_zero_baseline(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    frame = np.zeros((1, 1, 3), dtype=np.uint8)

    with patch("core.automation_supervisor.log") as runtime_log:
        effective_values = []
        for value, confidence in (
            (Decimal("0"), 96.0),
            (Decimal("362e12"), 84.0),
            (Decimal("4.05e15"), 84.0),
            (Decimal("7.52e15"), 80.0),
            (Decimal("10.2e15"), 84.0),
        ):
            _, _, _, effective = supervisor.process_coins(
                frame,
                value,
                confidence,
                True,
                allow_actions=False,
            )
            effective_values.append(effective)

    assert effective_values == [
        Decimal("0"),
        Decimal("362e12"),
        Decimal("362e12"),
        Decimal("7.52e15"),
        Decimal("10.2e15"),
    ]
    assert supervisor._last_coins_val == Decimal("10.2e15")
    assert supervisor._coins_pending_plausibility_val is None
    assert any(
        call.args
        and (
            "Accepted sustained rate change 362T → 7.52q after "
            "consecutive 4.05q and 7.52q readings"
        )
        in call.args[0]
        for call in runtime_log.call_args_list
    )


def test_supervisor_does_not_confirm_isolated_jump(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    supervisor._last_coins_val = Decimal("362e12")
    frame = np.zeros((1, 1, 3), dtype=np.uint8)

    _, _, _, held = supervisor.process_coins(
        frame,
        Decimal("4.05e15"),
        84.0,
        True,
        allow_actions=False,
    )
    _, _, _, recovered = supervisor.process_coins(
        frame,
        Decimal("400e12"),
        84.0,
        True,
        allow_actions=False,
    )

    assert held == Decimal("362e12")
    assert recovered == Decimal("400e12")
    assert supervisor._coins_pending_plausibility_val is None


def test_coin_display_toggle_requires_two_missing_min_samples(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    supervisor._last_coins_val = Decimal("362e12")
    frame = np.zeros((1, 1, 3), dtype=np.uint8)

    with (
        patch("core.automation_supervisor.time.time", return_value=100.0),
        patch(
            "core.automation_supervisor.tap_if_visible",
            return_value=False,
        ) as tap,
    ):
        _, _, _, first_effective = supervisor.process_coins(
            frame,
            Decimal("16.1e15"),
            56.0,
            False,
        )
        tap.assert_not_called()

        _, _, _, second_effective = supervisor.process_coins(
            frame,
            Decimal("16.3e15"),
            80.0,
            False,
        )

    assert first_effective == Decimal("362e12")
    assert second_effective == Decimal("362e12")
    tap.assert_called_once_with("buttons.coin_toggle", retries=1)


def test_coin_toggle_does_not_publish_total_as_rate(tmp_path):
    supervisor = AutomationSupervisor(
        control_file=str(tmp_path / "automation_ctl.json"),
        auto_return_enabled=False,
    )
    supervisor._last_coins_val = Decimal("362e12")
    supervisor._coins_has_min_miss = 1
    frame = np.zeros((1, 1, 3), dtype=np.uint8)

    with (
        patch("core.automation_supervisor.time.time", return_value=100.0),
        patch("core.automation_supervisor.tap_if_visible", return_value=True),
        patch("core.automation_supervisor.time.sleep"),
        patch(
            "core.automation_supervisor.capture_and_save_screenshot",
            return_value=frame,
        ),
        patch(
            "core.automation_supervisor.detect_coins_from_image",
            return_value=(Decimal("3.19"), 96.0, False),
        ),
    ):
        value, confidence, has_min, effective = supervisor.process_coins(
            frame,
            Decimal("16.1e15"),
            56.0,
            False,
        )

    assert value == Decimal("3.19")
    assert confidence == 96.0
    assert has_min is False
    assert effective == Decimal("362e12")
    assert supervisor._last_coins_val == Decimal("362e12")
