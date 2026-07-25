from pathlib import Path

import cv2
import numpy as np
import pytest

import core.card_recharge_modes as recharge
from core.card_recharge_modes import (
    CardRechargeMode,
    ensure_card_recharge_modes,
    measure_card_recharge_mode,
    normalize_card_recharge_modes,
)
from core.label_tapper import get_label_match
from core.state_detector import detect_state_and_overlays


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"
REQUIRED = {
    "Demon Mode": "auto_reactivate",
    "Nuke": "ready_after_recharge",
}


def _load(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    return image


DEMON_INVENTORY = "cards_inventory_demon_mode_visible_20260725.png"
NUKE_INVENTORY = "cards_inventory_nuke_visible_20260725.png"
DEMON_DETAIL = "card_demon_mode_recharge_auto_20260725.png"
NUKE_DETAIL = "card_nuke_recharge_manual_20260725.png"


def test_live_card_details_separate_reactivation_and_ready_after_recharge():
    demon = measure_card_recharge_mode(
        _load(DEMON_DETAIL),
        "Demon Mode",
        required="auto_reactivate",
    )
    nuke = measure_card_recharge_mode(
        _load(NUKE_DETAIL),
        "Nuke",
        required="ready_after_recharge",
    )

    assert demon.observed is CardRechargeMode.AUTO_REACTIVATE
    assert demon.valid
    assert demon.detail_confidence >= 0.88
    assert demon.checkbox_outline_pixels >= 350
    assert demon.checkmark_pixels >= 100

    assert nuke.observed is CardRechargeMode.READY_AFTER_RECHARGE
    assert nuke.valid
    assert nuke.detail_confidence >= 0.88
    assert nuke.checkbox_outline_pixels >= 350
    assert nuke.checkmark_pixels <= 30


@pytest.mark.parametrize(
    ("target", "fixture"),
    (
        ("buttons.card_inventory:demon_mode", DEMON_INVENTORY),
        ("buttons.card_inventory:nuke", NUKE_INVENTORY),
        ("indicators.card_detail:demon_mode", DEMON_DETAIL),
        ("indicators.card_detail:nuke", NUKE_DETAIL),
        ("buttons.card_detail:close", DEMON_DETAIL),
        ("buttons.card_detail:close", NUKE_DETAIL),
    ),
)
def test_live_card_templates_match_their_authoritative_fixture(target, fixture):
    match = get_label_match(target, screenshot=_load(fixture), return_meta=True)
    assert match["match_score"] >= 0.88


def test_card_detail_identity_is_required_for_checkbox_authority():
    wrong_detail = measure_card_recharge_mode(
        _load(NUKE_DETAIL),
        "Demon Mode",
        required="auto_reactivate",
    )
    inventory = measure_card_recharge_mode(
        _load(DEMON_INVENTORY),
        "Demon Mode",
        required="auto_reactivate",
    )

    assert wrong_detail.observed is CardRechargeMode.UNKNOWN
    assert not wrong_detail.detail_visible
    assert inventory.observed is CardRechargeMode.UNKNOWN
    assert not inventory.detail_visible


@pytest.mark.parametrize(
    "raw",
    (
        None,
        {"Demon Mode": "auto_reactivate"},
        {"Demon Mode": "auto_reactivate", "Nuke": "again"},
        {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
            "Other": "ready_after_recharge",
        },
        {"Demon Mode": "auto", "Nuke": "manual"},
    ),
)
def test_invalid_card_recharge_contracts_are_rejected(raw):
    with pytest.raises(ValueError, match="card_recharge_modes"):
        normalize_card_recharge_modes(raw)


class _CardRouter:
    def __init__(self, *, mismatched: bool):
        self.top = _load("cards_gc_active_20260713.png")
        self.demon_inventory = _load(DEMON_INVENTORY)
        self.nuke_inventory = _load(NUKE_INVENTORY)
        self.demon_auto_reactivate = _load(DEMON_DETAIL)
        self.nuke_ready_after_recharge = _load(NUKE_DETAIL)
        self.demon_detail = self.demon_auto_reactivate.copy()
        self.nuke_detail = self.nuke_ready_after_recharge.copy()
        if mismatched:
            x, y, width, height = recharge._CHECKMARK_REGION
            self.demon_detail[y : y + height, x : x + width] = (
                self.nuke_ready_after_recharge[y : y + height, x : x + width]
            )
            self.nuke_detail[y : y + height, x : x + width] = (
                self.demon_auto_reactivate[y : y + height, x : x + width]
            )
        self.current = self.top
        self.detail_label = None
        self.long_presses = []
        self.checkbox_taps = []
        self.close_taps = []
        self.swipes = []

    def capture(self):
        return self.current

    def swipe(self, target):
        self.swipes.append(target)
        if target == "gesture_targets.goto_top:cards_inventory":
            self.current = self.top
            return True
        assert target == "gesture_targets.goto_next:cards_inventory"
        if self.current is self.top:
            self.current = self.demon_inventory
        else:
            self.current = self.nuke_inventory
        return True

    def long_press(self, target, **kwargs):
        get_label_match(target, screenshot=kwargs["screenshot"])
        self.long_presses.append(target)
        if target == "buttons.card_inventory:demon_mode":
            self.detail_label = "Demon Mode"
            self.current = self.demon_detail
        else:
            assert target == "buttons.card_inventory:nuke"
            self.detail_label = "Nuke"
            self.current = self.nuke_detail
        return True

    def safe_tap(self, target, **kwargs):
        if isinstance(target, tuple):
            assert target == recharge._CHECKBOX_POINT
            assert kwargs["verification"].authorizes(target)
            self.checkbox_taps.append(self.detail_label)
            if self.detail_label == "Demon Mode":
                self.current = self.demon_auto_reactivate
            else:
                self.current = self.nuke_ready_after_recharge
            return True

        assert target == "buttons.card_detail:close"
        get_label_match(target, screenshot=kwargs["screenshot"])
        self.close_taps.append(self.detail_label)
        if self.detail_label == "Demon Mode":
            self.current = self.demon_inventory
        else:
            self.current = self.nuke_inventory
        self.detail_label = None
        return True


def _ensure(router: _CardRouter):
    return ensure_card_recharge_modes(
        REQUIRED,
        cards_screenshot=router.current,
        capture_fn=router.capture,
        detector=detect_state_and_overlays,
        safe_long_press_fn=router.long_press,
        safe_tap_fn=router.safe_tap,
        swipe_fn=router.swipe,
        sleep_fn=lambda _seconds: None,
    )


def test_matching_card_recharge_modes_are_verified_without_toggling():
    router = _CardRouter(mismatched=False)

    result = _ensure(router)

    assert result.valid
    assert not result.changed
    assert result.changed_labels == ()
    assert router.checkbox_taps == []
    assert router.long_presses == [
        "buttons.card_inventory:demon_mode",
        "buttons.card_inventory:nuke",
    ]
    assert router.close_taps == ["Demon Mode", "Nuke"]
    assert result.as_dict()["modes"] == [
        {
            "label": "Demon Mode",
            "required": "auto_reactivate",
            "observed": "auto_reactivate",
            "detail_visible": True,
            "detail_confidence": pytest.approx(1.0),
            "checkbox_outline_pixels": 462,
            "checkmark_pixels": 282,
            "valid": True,
        },
        {
            "label": "Nuke",
            "required": "ready_after_recharge",
            "observed": "ready_after_recharge",
            "detail_visible": True,
            "detail_confidence": pytest.approx(1.0),
            "checkbox_outline_pixels": 462,
            "checkmark_pixels": 0,
            "valid": True,
        },
    ]


def test_mismatched_card_recharge_modes_are_toggled_and_reverified():
    router = _CardRouter(mismatched=True)

    result = _ensure(router)

    assert result.valid
    assert result.changed
    assert result.changed_labels == ("Demon Mode", "Nuke")
    assert router.checkbox_taps == ["Demon Mode", "Nuke"]
    assert [mode.observed.value for mode in result.modes] == [
        "auto_reactivate",
        "ready_after_recharge",
    ]
