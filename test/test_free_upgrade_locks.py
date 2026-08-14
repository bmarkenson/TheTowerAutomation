from pathlib import Path

import cv2
import numpy as np
import pytest

from core.clickmap_access import get_click
from core.free_upgrade_locks import (
    FARM_FREE_UPGRADE_LOCKS,
    FreeUpgradeLockEvidence,
    FreeUpgradeLockState,
    inspect_free_upgrade_locks,
    measure_free_upgrade_lock,
    measure_unavailable_free_upgrade_lock,
    normalize_free_upgrade_lock_requirements,
)
from core.upgrade_box_detector import UpgradeBox, detect_visible_boxes


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "free_upgrade_locks"


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    return image


def _evidence(label: str, state: FreeUpgradeLockState):
    return FreeUpgradeLockEvidence(
        label=label,
        state=state,
        title_text=label,
        title_confidence=96.0,
        lock_text="Lock Level (1)",
        lock_confidence=95.0,
        checkbox_outline_pixels=1_800,
        checkmark_pixels=650 if state is FreeUpgradeLockState.CHECKED else 0,
    )


def test_live_checked_and_unchecked_lock_fixtures_are_separated():
    checked = measure_free_upgrade_lock(
        _load("shockwave_size_checked_20260720.png"),
        "Shockwave Size",
    )
    unchecked = measure_free_upgrade_lock(
        _load("shockwave_size_unchecked_20260720.png"),
        "Shockwave Size",
    )

    assert checked.state is FreeUpgradeLockState.CHECKED
    assert checked.valid
    assert checked.checkmark_pixels >= 600
    assert checked.checkbox_outline_pixels >= 1_800
    assert unchecked.state is FreeUpgradeLockState.UNCHECKED
    assert unchecked.authoritative_mismatch
    assert unchecked.checkmark_pixels == 0
    assert unchecked.checkbox_outline_pixels >= 1_800


def test_lock_fixture_cannot_authorize_a_different_upgrade_title():
    evidence = measure_free_upgrade_lock(
        _load("shockwave_size_checked_20260720.png"),
        "Bounce Shot Range",
    )

    assert evidence.state is FreeUpgradeLockState.UNKNOWN
    assert not evidence.valid
    assert not evidence.authoritative_mismatch


def test_locked_range_tree_makes_bounce_lock_explicitly_unavailable(monkeypatch):
    monkeypatch.setattr(
        "core.free_upgrade_locks.ocr_text_and_conf",
        lambda _crop, psm: ("Unlock Range Upgrades 50 coins", 83.0),
    )

    evidence = measure_unavailable_free_upgrade_lock(
        np.zeros((1920, 1080, 3), dtype=np.uint8),
        "Bounce Shot Targets",
    )

    assert evidence is not None
    assert evidence.state is FreeUpgradeLockState.UNAVAILABLE
    assert not evidence.valid
    assert not evidence.authoritative_mismatch
    assert measure_unavailable_free_upgrade_lock(
        np.zeros((1920, 1080, 3), dtype=np.uint8),
        "Shockwave Size",
    ) is None


@pytest.mark.parametrize(
    ("fixture", "label"),
    (
        ("bounce_shot_targets_unchecked_20260720.png", "Bounce Shot Targets"),
        ("bounce_shot_range_unchecked_20260720.png", "Bounce Shot Range"),
    ),
)
def test_live_bounce_shot_fixtures_are_authoritative_unchecked(fixture, label):
    evidence = measure_free_upgrade_lock(_load(fixture), label)

    assert evidence.state is FreeUpgradeLockState.UNCHECKED
    assert evidence.title_text == label
    assert evidence.title_confidence >= 95.0
    assert evidence.checkbox_outline_pixels >= 1_800
    assert evidence.checkmark_pixels == 0


def test_farm_lock_contract_rejects_missing_duplicate_and_unknown_labels():
    assert normalize_free_upgrade_lock_requirements(
        FARM_FREE_UPGRADE_LOCKS,
        require_farm_set=True,
    ) == FARM_FREE_UPGRADE_LOCKS

    with pytest.raises(ValueError, match="must contain"):
        normalize_free_upgrade_lock_requirements(
            FARM_FREE_UPGRADE_LOCKS[:-1],
            require_farm_set=True,
        )
    with pytest.raises(ValueError, match="duplicates"):
        normalize_free_upgrade_lock_requirements(
            ["Shockwave Size", "Shockwave Size"]
        )
    with pytest.raises(ValueError, match="unsupported"):
        normalize_free_upgrade_lock_requirements(["Health"])


def test_workshop_lock_action_coordinates_are_explicit():
    assert get_click("navigation.workshop:upgrade") == (270, 343)
    assert get_click("navigation.workshop:attack") == (135, 1685)
    assert get_click("navigation.workshop:defense") == (405, 1685)
    assert get_click("buttons.free_upgrade_lock:checkbox") == (257, 998)


def test_workshop_scan_region_finds_shockwave_above_battle_viewport():
    image = _load("shockwave_size_visible_workshop_20260720.png")

    boxes = detect_visible_boxes(
        image,
        menu="defense",
        column_regions={
            "left": (26, 490, 511, 1125),
            "right": (546, 490, 513, 1125),
        },
    )

    shockwave = next(box for box in boxes["left"] if box.text == "Shockwave Size")
    assert shockwave.rect[1] < 1253
    assert shockwave.confidence >= 95.0


class _WorkshopUi:
    def __init__(self, states, *, mode="upgrade", menu="attack"):
        self.frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        self.mode = mode
        self.menu = menu
        self.detail_label = None
        self.states = dict(states)
        self.actions = []
        self.last_box_label = None

    def capture(self):
        return self.frame

    def detect(self, _frame):
        return {
            "state": "WORKSHOP",
            "secondary_states": ["WORKSHOP_FARM_SLOT"],
            "overlays": ["UPGRADE_DETAIL"] if self.detail_label else [],
        }

    def measure_menu(self, _frame):
        if self.detail_label or self.mode != "upgrade":
            return None
        return self.menu

    def detect_boxes(self, _frame, *, menu, column_regions=None):
        assert column_regions == {
            "left": (26, 490, 511, 1125),
            "right": (546, 490, 513, 1125),
        }
        specs = {
            "Shockwave Size": ("defense", "left", (26, 1320, 511, 240)),
            "Bounce Shot Targets": ("attack", "right", (546, 1320, 509, 240)),
            "Bounce Shot Range": ("attack", "left", (26, 1320, 511, 240)),
        }
        result = {"left": [], "right": []}
        for label, (expected_menu, column, rect) in specs.items():
            if self.mode == "upgrade" and menu == self.menu == expected_menu:
                result[column].append(UpgradeBox(column, rect, text=label))
        return result

    def measure_lock(self, _frame, expected_label):
        if self.detail_label != expected_label:
            return _evidence(expected_label, FreeUpgradeLockState.UNKNOWN)
        return _evidence(expected_label, self.states[expected_label])

    def tap(self, target, **kwargs):
        self.actions.append((target, kwargs.get("log_label")))
        if target == "navigation.workshop:upgrade":
            self.mode = "upgrade"
            return True
        if target == "navigation.workshop:attack":
            self.menu = "attack"
            return True
        if target == "navigation.workshop:defense":
            self.menu = "defense"
            return True
        if isinstance(target, tuple):
            x, _y = target
            if self.menu == "defense":
                self.detail_label = "Shockwave Size"
            elif x > 500:
                self.detail_label = "Bounce Shot Targets"
            else:
                self.detail_label = "Bounce Shot Range"
            return True
        if target == "buttons.free_upgrade_lock:checkbox":
            assert self.detail_label is not None
            self.states[self.detail_label] = FreeUpgradeLockState.CHECKED
            return True
        if target == "gesture_targets.upgrade_detail_dismiss":
            self.detail_label = None
            return True
        return False


def test_inspection_returns_from_enhance_to_upgrade_before_selecting_category():
    ui = _WorkshopUi(
        {label: FreeUpgradeLockState.CHECKED for label in FARM_FREE_UPGRADE_LOCKS},
        mode="enhance",
    )

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid
    assert ui.mode == "upgrade"
    assert [action for action, _label in ui.actions[:2]] == [
        "navigation.workshop:upgrade",
        "navigation.workshop:defense",
    ]


def test_read_only_inspection_reports_unchecked_without_toggling():
    ui = _WorkshopUi(
        {
            "Shockwave Size": FreeUpgradeLockState.CHECKED,
            "Bounce Shot Targets": FreeUpgradeLockState.UNCHECKED,
            "Bounce Shot Range": FreeUpgradeLockState.CHECKED,
        }
    )

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        sleep_fn=lambda _seconds: None,
    )

    assert not result.evidence.valid
    assert result.evidence.has_authoritative_mismatch
    assert result.changed_labels == ()
    assert not any(
        action == "buttons.free_upgrade_lock:checkbox"
        for action, _label in ui.actions
    )
    assert ui.detail_label is None


def test_inspection_records_locked_range_tree_without_scanning_for_bounce_locks():
    ui = _WorkshopUi(
        {label: FreeUpgradeLockState.CHECKED for label in FARM_FREE_UPGRADE_LOCKS}
    )
    swipes = []

    def measure_unavailable(_frame, label):
        if not label.startswith("Bounce Shot"):
            return None
        return FreeUpgradeLockEvidence(
            label=label,
            state=FreeUpgradeLockState.UNAVAILABLE,
            title_text="Unlock Range Upgrades 50 coins",
            title_confidence=83.0,
            lock_text="Range Upgrades locked",
            lock_confidence=83.0,
            checkbox_outline_pixels=0,
            checkmark_pixels=0,
        )

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *args: swipes.append(args),
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        measure_unavailable_fn=measure_unavailable,
        sleep_fn=lambda _seconds: None,
    )

    assert [lock.state for lock in result.evidence.locks] == [
        FreeUpgradeLockState.CHECKED,
        FreeUpgradeLockState.UNAVAILABLE,
        FreeUpgradeLockState.UNAVAILABLE,
    ]
    assert not result.evidence.valid
    assert not result.evidence.has_authoritative_mismatch
    assert swipes == []
    detail_taps = [action for action, _label in ui.actions if isinstance(action, tuple)]
    assert len(detail_taps) == 1


def test_transient_unchecked_frame_does_not_authorize_repair():
    ui = _WorkshopUi(
        {label: FreeUpgradeLockState.CHECKED for label in FARM_FREE_UPGRADE_LOCKS}
    )
    shockwave_reads = 0

    def settling_measure(frame, expected_label):
        nonlocal shockwave_reads
        if ui.detail_label == expected_label == "Shockwave Size":
            shockwave_reads += 1
            if shockwave_reads == 1:
                return _evidence(
                    expected_label,
                    FreeUpgradeLockState.UNCHECKED,
                )
        return ui.measure_lock(frame, expected_label)

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=settling_measure,
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid
    assert not result.evidence.has_authoritative_mismatch
    assert shockwave_reads >= 3
    assert not any(
        action == "buttons.free_upgrade_lock:checkbox"
        for action, _label in ui.actions
    )


def test_reconfirmation_uses_fresh_position_after_workshop_scroll_settles():
    ui = _WorkshopUi(
        {label: FreeUpgradeLockState.CHECKED for label in FARM_FREE_UPGRADE_LOCKS}
    )
    base_detect = ui.detect_boxes
    shockwave_scans = 0

    def settling_detect(frame, *, menu, column_regions=None):
        nonlocal shockwave_scans
        boxes = base_detect(
            frame,
            menu=menu,
            column_regions=column_regions,
        )
        if menu == "defense":
            shockwave_scans += 1
            if shockwave_scans >= 2:
                box = boxes["left"][0]
                x, y, width, height = box.rect
                box.rect = (x, y - 80, width, height)
        return boxes

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=settling_detect,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid
    assert shockwave_scans == 2
    assert ui.actions[1][0] == (153, 1360)


def test_detail_tap_verifier_reuses_full_home_workshop_scan_regions():
    ui = _WorkshopUi(
        {label: FreeUpgradeLockState.CHECKED for label in FARM_FREE_UPGRADE_LOCKS}
    )
    ui.frame[:] = 20

    def verified_tap(target, **kwargs):
        verification = kwargs.get("verification")
        if isinstance(target, tuple):
            assert verification is not None
            assert verification.authorizes(target)
        return ui.tap(target, **kwargs)

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=verified_tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid


def test_no_battle_enforcement_checks_only_authoritative_mismatch():
    ui = _WorkshopUi(
        {
            "Shockwave Size": FreeUpgradeLockState.CHECKED,
            "Bounce Shot Targets": FreeUpgradeLockState.UNCHECKED,
            "Bounce Shot Range": FreeUpgradeLockState.CHECKED,
        }
    )

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        enforce=True,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid
    assert result.changed_labels == ("Bounce Shot Targets",)
    assert [
        action
        for action, _label in ui.actions
        if action == "buttons.free_upgrade_lock:checkbox"
    ] == ["buttons.free_upgrade_lock:checkbox"]
    assert ui.detail_label is None


def test_free_upgrade_repair_observer_runs_before_checkbox_input():
    ui = _WorkshopUi(
        {
            "Shockwave Size": FreeUpgradeLockState.CHECKED,
            "Bounce Shot Targets": FreeUpgradeLockState.UNCHECKED,
            "Bounce Shot Range": FreeUpgradeLockState.CHECKED,
        }
    )
    events = []

    def tracked_tap(target, **kwargs):
        if target == "buttons.free_upgrade_lock:checkbox":
            events.append("checkbox")
        return ui.tap(target, **kwargs)

    result = inspect_free_upgrade_locks(
        FARM_FREE_UPGRADE_LOCKS,
        screenshot=ui.frame,
        enforce=True,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=tracked_tap,
        swipe_fn=lambda *_args: None,
        detect_boxes_fn=ui.detect_boxes,
        measure_menu_fn=ui.measure_menu,
        measure_lock_fn=ui.measure_lock,
        repair_observer_fn=lambda: events.append("observer"),
        sleep_fn=lambda _seconds: None,
    )

    assert result.evidence.valid
    assert events == ["observer", "checkbox"]


def test_free_upgrade_repair_observer_failure_sends_no_checkbox_input():
    ui = _WorkshopUi(
        {
            "Shockwave Size": FreeUpgradeLockState.CHECKED,
            "Bounce Shot Targets": FreeUpgradeLockState.UNCHECKED,
            "Bounce Shot Range": FreeUpgradeLockState.CHECKED,
        }
    )

    def reject_repair():
        raise RuntimeError("save invalidation failed")

    with pytest.raises(RuntimeError, match="save invalidation failed"):
        inspect_free_upgrade_locks(
            FARM_FREE_UPGRADE_LOCKS,
            screenshot=ui.frame,
            enforce=True,
            capture_fn=ui.capture,
            detector=ui.detect,
            safe_tap_fn=ui.tap,
            swipe_fn=lambda *_args: None,
            detect_boxes_fn=ui.detect_boxes,
            measure_menu_fn=ui.measure_menu,
            measure_lock_fn=ui.measure_lock,
            repair_observer_fn=reject_repair,
            sleep_fn=lambda _seconds: None,
        )

    assert all(
        action != "buttons.free_upgrade_lock:checkbox"
        for action, _label in ui.actions
    )
