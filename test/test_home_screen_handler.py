from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest

from core.input import TapDispatchOutcome, TapDispatchStatus, TapVerification
from core.matcher import get_match
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import (
    HOME_TIER_SELECTOR_REGION,
    HomeBattleEvidence,
    HomeTierEvidence,
    detect_home_battle_control,
    detect_home_tier,
)
from handlers.home_screen_handler import (
    HomeTierSelectionResult,
    _tap_verified_home_battle_control,
    handle_home_screen,
    select_verified_home_tier,
    tap_verified_new_battle,
)


ROOT = Path(__file__).resolve().parents[1]
NEW_DAY_HOME_FIXTURE = (
    ROOT / "test" / "fixtures" / "home_screen_new_day_store_badge_20260713.png"
)


def _screenshot():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_verified_home_battle_ocr_fallback_taps_configured_control():
    action_guard = lambda: True
    with (
        patch("handlers.home_screen_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
                "BATTLE",
            ),
        ),
        patch("handlers.home_screen_handler.safe_tap", return_value=True) as tap,
    ):
        assert _tap_verified_home_battle_control(
            action_guard_fn=action_guard,
        )

    tap.assert_called_once()
    target, = tap.call_args.args
    kwargs = tap.call_args.kwargs
    assert target == "buttons.battle_control:home"
    assert kwargs["dispatch"] == "now"
    assert kwargs["action_guard_fn"] is action_guard
    verification = kwargs["verification"]
    assert isinstance(verification, TapVerification)
    assert verification.description == "home_battle_control:NEW_BATTLE"


def test_home_battle_fallback_refuses_unknown_screen():
    with (
        patch("handlers.home_screen_handler.capture_adb_screenshot", return_value=_screenshot()),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "UNKNOWN"},
        ),
        patch("handlers.home_screen_handler.safe_tap") as tap,
    ):
        assert not _tap_verified_home_battle_control()

    tap.assert_not_called()


def test_home_battle_alternative_probes_keep_misses_diagnostic():
    with (
        patch(
            "handlers.home_screen_handler.tap_if_visible",
            side_effect=(False, True),
        ) as tap,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        handle_home_screen()

    assert [call.args[0] for call in tap.call_args_list] == [
        "buttons.battle:home",
        "buttons.resume_battle:home",
    ]
    assert all(
        call.kwargs["failure_log_level"] == "DEBUG"
        for call in tap.call_args_list
    )
    assert all(
        call.kwargs["return_dispatch_outcome"] is True
        for call in tap.call_args_list
    )


def test_home_battle_probe_preserves_uncertainty_without_fallback_replay():
    uncertain = TapDispatchOutcome(TapDispatchStatus.UNCERTAIN)
    with (
        patch(
            "handlers.home_screen_handler.tap_if_visible",
            return_value=uncertain,
        ) as tap,
        patch(
            "handlers.home_screen_handler._tap_verified_home_battle_control"
        ) as fallback,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        result = handle_home_screen(return_dispatch_outcome=True)

    assert result is uncertain
    tap.assert_called_once()
    fallback.assert_not_called()


def test_disabled_auto_start_is_silent_in_low_level_handler():
    with patch("handlers.home_screen_handler.log") as emit:
        assert handle_home_screen(restart_enabled=False) is False

    emit.assert_not_called()


def test_validation_new_battle_tap_refuses_resume_control():
    with (
        patch(
            "handlers.home_screen_handler.capture_adb_screenshot",
            return_value=_screenshot(),
        ),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.RESUME_BATTLE,
                "ocr",
                96.0,
                "RESUME BATTLE",
            ),
        ),
        patch("handlers.home_screen_handler.safe_tap") as tap,
    ):
        assert not tap_verified_new_battle()

    tap.assert_not_called()


def test_explicit_attach_refuses_a_new_battle_control():
    with (
        patch(
            "handlers.home_screen_handler.capture_adb_screenshot",
            return_value=_screenshot(),
        ),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
                "BATTLE",
            ),
        ),
        patch("handlers.home_screen_handler.safe_tap") as tap,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        assert not handle_home_screen(
            restart_enabled=True,
            require_resume_battle=True,
        )

    tap.assert_not_called()


def test_home_handler_rejects_conflicting_explicit_intents():
    with pytest.raises(ValueError, match="both"):
        handle_home_screen(
            restart_enabled=True,
            require_new_battle=True,
            require_resume_battle=True,
        )


def test_explicit_home_dispatch_emits_one_correlated_action_result_pair():
    with (
        patch(
            "handlers.home_screen_handler.tap_verified_new_battle",
            return_value=True,
        ),
        patch("handlers.home_screen_handler.log_action_intent") as action,
        patch("handlers.home_screen_handler.log_result") as result,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        assert handle_home_screen(
            restart_enabled=True,
            require_new_battle=True,
            operation_id="workflow-1:observation-1:home_dispatch",
            action_purpose="Starting a new battle",
            action_reason="the exact operator intent passed normal gates",
        ) is True

    action.assert_called_once_with(
        "Starting a new battle",
        reason="the exact operator intent passed normal gates",
        operation_id="workflow-1:observation-1:home_dispatch",
    )
    result.assert_called_once_with(
        "Verified Home battle control dispatched",
        operation_id="workflow-1:observation-1:home_dispatch",
    )


def test_resume_battle_ocr_tolerates_button_border_artifacts():
    with patch(
        "core.home_battle.ocr_text_and_conf",
        return_value=("L RESUME BATTLE J", 93.75),
    ):
        evidence = detect_home_battle_control(_screenshot())

    assert evidence.control is HomeBattleControl.RESUME_BATTLE
    assert evidence.source == "ocr"


def test_live_new_day_home_fixture_matches_battle_and_store_badge():
    screenshot = cv2.imread(str(NEW_DAY_HOME_FIXTURE))
    assert screenshot is not None

    battle_point, battle_confidence = get_match(
        "buttons.battle:home",
        screenshot=screenshot,
    )
    badge_point, badge_confidence = get_match(
        "overlays.daily_free_gems_badge_home",
        screenshot=screenshot,
    )

    assert battle_point is not None
    assert battle_confidence >= 0.9
    assert badge_point is not None
    assert badge_confidence >= 0.9


def test_live_new_day_home_fixture_classifies_as_new_battle():
    screenshot = cv2.imread(str(NEW_DAY_HOME_FIXTURE))
    assert screenshot is not None

    evidence = detect_home_battle_control(screenshot)

    assert evidence.control is HomeBattleControl.NEW_BATTLE


def test_live_new_day_home_fixture_reads_exact_tier():
    screenshot = cv2.imread(str(NEW_DAY_HOME_FIXTURE))
    assert screenshot is not None

    evidence = detect_home_tier(screenshot)

    assert evidence.tier == 18
    assert evidence.source == "ocr"
    assert evidence.confidence >= 55.0


@pytest.mark.parametrize(
    ("raw_text", "confidence"),
    (("Tier eighteen", 96.0), ("Tier 18", 40.0), ("Tier 101", 96.0)),
)
def test_home_tier_reader_refuses_inexact_evidence(raw_text, confidence):
    with patch(
        "core.home_battle.ocr_text_and_conf",
        return_value=(raw_text, confidence),
    ):
        evidence = detect_home_tier(_screenshot())

    assert evidence.tier is None


def test_home_tier_selection_steps_from_14_to_19_with_fresh_postconditions():
    frames = [_screenshot() for _ in range(6)]
    capture = Mock(side_effect=frames)
    sleep = Mock()
    action_guard = Mock(return_value=True)
    tiers = [
        HomeTierEvidence(tier, "ocr", 96.0, f"Tier {tier}")
        for tier in range(14, 20)
    ]
    with (
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
                "BATTLE",
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            side_effect=tiers,
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.DISPATCHED),
        ) as tap,
    ):
        result = select_verified_home_tier(
            19,
            action_guard_fn=action_guard,
            capture_fn=capture,
            sleep_fn=sleep,
        )

    assert result == HomeTierSelectionResult(
        True,
        TapDispatchStatus.DISPATCHED,
        19,
        5,
        "verified Home Tier 19 after 5 selector taps",
    )
    assert capture.call_count == 6
    assert tap.call_count == 5
    assert [item.args[0] for item in tap.call_args_list] == [
        "buttons.home_tier:increase"
    ] * 5
    for index, item in enumerate(tap.call_args_list, start=14):
        assert item.kwargs["action_guard_fn"] is action_guard
        assert item.kwargs["return_dispatch_outcome"] is True
        verification = item.kwargs["verification"]
        assert verification.screenshot is frames[index - 14]
        assert verification.target_region == HOME_TIER_SELECTOR_REGION
        assert verification.description == f"home_new_battle_tier:{index}->{index + 1}"


def test_home_tier_selection_uses_decrease_control():
    frames = [_screenshot(), _screenshot()]
    with (
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            side_effect=(
                HomeTierEvidence(19, "ocr", 96.0, "Tier 19"),
                HomeTierEvidence(18, "ocr", 96.0, "Tier 18"),
            ),
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.DISPATCHED),
        ) as tap,
    ):
        result = select_verified_home_tier(
            18,
            capture_fn=Mock(side_effect=frames),
            sleep_fn=Mock(),
        )

    assert result.verified
    tap.assert_called_once()
    assert tap.call_args.args == ("buttons.home_tier:decrease",)


def test_home_tier_exact_postcondition_resolves_dispatch_uncertainty():
    with (
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            side_effect=(
                HomeTierEvidence(18, "ocr", 96.0, "Tier 18"),
                HomeTierEvidence(19, "ocr", 96.0, "Tier 19"),
            ),
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.UNCERTAIN),
        ),
    ):
        result = select_verified_home_tier(
            19,
            capture_fn=Mock(side_effect=[_screenshot(), _screenshot()]),
            sleep_fn=Mock(),
        )

    assert result.verified
    assert result.status is TapDispatchStatus.DISPATCHED
    assert result.observed_tier == 19
    assert result.taps == 1


def test_home_tier_selection_stable_no_change_blocks_battle_without_uncertainty():
    with (
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            return_value=HomeTierEvidence(14, "ocr", 96.0, "Tier 14"),
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.DISPATCHED),
        ),
    ):
        result = select_verified_home_tier(
            19,
            capture_fn=Mock(side_effect=[_screenshot() for _ in range(7)]),
            sleep_fn=Mock(),
        )

    assert not result.verified
    assert result.status is TapDispatchStatus.NOT_DISPATCHED
    assert result.observed_tier == 14
    assert result.taps == 1


def test_home_tier_selection_preserves_unresolved_input_uncertainty():
    evidence = [HomeTierEvidence(14, "ocr", 96.0, "Tier 14")]
    evidence.extend(
        HomeTierEvidence(None, "ocr_unrecognized", 10.0, "")
        for _ in range(6)
    )
    with (
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            side_effect=evidence,
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.UNCERTAIN),
        ),
    ):
        result = select_verified_home_tier(
            19,
            capture_fn=Mock(side_effect=[_screenshot() for _ in range(7)]),
            sleep_fn=Mock(),
        )

    assert not result.verified
    assert result.status is TapDispatchStatus.UNCERTAIN
    assert result.taps == 1


def test_required_tier_is_reverified_at_final_battle_input_boundary():
    action_guard = Mock(return_value=True)
    with (
        patch(
            "handlers.home_screen_handler.capture_adb_screenshot",
            return_value=_screenshot(),
        ),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
                "BATTLE",
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            return_value=HomeTierEvidence(19, "ocr", 96.0, "Tier 19"),
        ),
        patch(
            "handlers.home_screen_handler.safe_tap",
            return_value=TapDispatchOutcome(TapDispatchStatus.DISPATCHED),
        ) as tap,
        patch("core.input.is_complete_screenshot", return_value=True),
    ):
        result = tap_verified_new_battle(
            required_tier=19,
            action_guard_fn=action_guard,
            return_dispatch_outcome=True,
        )
        verification = tap.call_args.kwargs["verification"]
        final_boundary_authorized = verification.authorizes((540, 1550))

    assert result.dispatched
    assert final_boundary_authorized
    assert tap.call_args.kwargs["action_guard_fn"] is action_guard


def test_required_tier_mismatch_refuses_final_battle_tap():
    with (
        patch(
            "handlers.home_screen_handler.capture_adb_screenshot",
            return_value=_screenshot(),
        ),
        patch(
            "handlers.home_screen_handler.detect_state_and_overlays",
            return_value={"state": "HOME_SCREEN"},
        ),
        patch(
            "handlers.home_screen_handler.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "ocr",
                96.0,
                "BATTLE",
            ),
        ),
        patch(
            "handlers.home_screen_handler.detect_home_tier",
            return_value=HomeTierEvidence(14, "ocr", 96.0, "Tier 14"),
        ),
        patch("handlers.home_screen_handler.safe_tap") as tap,
    ):
        result = tap_verified_new_battle(
            required_tier=19,
            return_dispatch_outcome=True,
        )

    assert result.status is TapDispatchStatus.NOT_DISPATCHED
    tap.assert_not_called()


def test_home_handler_keeps_tier_inputs_inside_one_action_result_pair():
    selection = HomeTierSelectionResult(
        True,
        TapDispatchStatus.DISPATCHED,
        19,
        5,
        "verified",
    )
    with (
        patch(
            "handlers.home_screen_handler.select_verified_home_tier",
            return_value=selection,
        ) as select,
        patch(
            "handlers.home_screen_handler.tap_verified_new_battle",
            return_value=True,
        ) as launch,
        patch("handlers.home_screen_handler.log_action_intent") as action,
        patch("handlers.home_screen_handler.log_result") as result,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        assert handle_home_screen(
            require_new_battle=True,
            required_tier=19,
            operation_id="start-1:home-1:home_dispatch",
            action_purpose="Starting a new battle",
            action_reason="execute the exact requested battle",
        )

    select.assert_called_once_with(19, action_guard_fn=None)
    launch.assert_called_once_with(
        required_tier=19,
        action_guard_fn=None,
    )
    action.assert_called_once()
    result.assert_called_once_with(
        "Verified Home Tier 19 and New Battle control dispatched",
        operation_id="start-1:home-1:home_dispatch",
    )


def test_home_handler_never_taps_battle_after_uncertain_tier_selection():
    selection = HomeTierSelectionResult(
        False,
        TapDispatchStatus.UNCERTAIN,
        None,
        1,
        "postcondition unavailable",
    )
    with (
        patch(
            "handlers.home_screen_handler.select_verified_home_tier",
            return_value=selection,
        ),
        patch(
            "handlers.home_screen_handler.tap_verified_new_battle",
        ) as launch,
        patch("handlers.home_screen_handler.time.sleep"),
    ):
        result = handle_home_screen(
            require_new_battle=True,
            required_tier=19,
            return_dispatch_outcome=True,
        )

    assert result.status is TapDispatchStatus.UNCERTAIN
    launch.assert_not_called()
