from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.battle_lifecycle import HomeBattleControl
from core.no_strategy_post_run import (
    NoStrategyPostRunError,
    PERK_TABS,
    capture_post_run_perk_configuration,
    inspect_post_run_free_upgrade_locks,
)


def _frame():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def test_post_run_lock_inspection_is_read_only_and_restores_new_battle_home():
    home = _frame()
    workshop = _frame()
    restored = _frame()
    captures = iter((workshop, restored))
    taps = []
    observed_enforce = []

    def detector(frame):
        return {"state": "WORKSHOP" if frame is workshop else "HOME_SCREEN"}

    def inspect(requirements, **kwargs):
        observed_enforce.append(kwargs["enforce"])
        assert tuple(requirements) == (
            "Shockwave Size",
            "Bounce Shot Targets",
            "Bounce Shot Range",
        )
        return SimpleNamespace(
            evidence=SimpleNamespace(
                as_dict=lambda: {
                    "locks": [
                        {"label": "Shockwave Size", "state": "checked"},
                    ]
                }
            ),
            changed_labels=(),
        )

    result = inspect_post_run_free_upgrade_locks(
        home,
        capture_fn=lambda: next(captures),
        detector=detector,
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        safe_tap_fn=lambda target, **_kwargs: taps.append(target) or True,
        inspect_fn=inspect,
        sleep_fn=lambda _seconds: None,
    )

    assert observed_enforce == [False]
    assert taps == ["navigation.goto_workshop_home", "navigation.goto_home"]
    assert result.values["boundary"] == "NEW_BATTLE"
    assert result.values["changed_labels"] == []
    assert result.home_screenshot is restored


def test_post_run_lock_inspection_rejects_resume_battle_boundary():
    with pytest.raises(NoStrategyPostRunError, match="requires NEW_BATTLE"):
        inspect_post_run_free_upgrade_locks(
            _frame(),
            detector=lambda _frame: {"state": "HOME_SCREEN"},
            home_control_fn=lambda _frame: SimpleNamespace(
                control=HomeBattleControl.RESUME_BATTLE
            ),
        )


def test_perk_configuration_capture_records_all_tabs_as_raw_evidence(tmp_path):
    phase = {"state": "PERKS", "active": 0}
    perk_frame = _frame()
    cards = _frame()
    home = _frame()

    def draw_active():
        perk_frame[:] = 0
        green = cv2.cvtColor(
            np.uint8([[[55, 220, 220]]]), cv2.COLOR_HSV2BGR
        )[0, 0]
        cyan = cv2.cvtColor(
            np.uint8([[[95, 220, 220]]]), cv2.COLOR_HSV2BGR
        )[0, 0]
        for index, (_field, _label, (x, y, width, height)) in enumerate(PERK_TABS):
            color = green if index == phase["active"] else cyan
            cv2.rectangle(
                perk_frame,
                (x, y),
                (x + width - 1, y + height - 1),
                tuple(int(value) for value in color),
                12,
            )

    draw_active()

    def detector(frame):
        if phase["state"] == "PERKS":
            return {"state": "PERKS"}
        if phase["state"] == "CARDS":
            return {"state": "CARDS"}
        return {"state": "HOME_SCREEN"}

    def capture():
        if phase["state"] == "PERKS":
            return perk_frame.copy()
        if phase["state"] == "CARDS":
            return cards
        return home

    def tap(target, **_kwargs):
        if isinstance(target, tuple):
            centers = [
                (x + width // 2, y + height // 2)
                for _field, _label, (x, y, width, height) in PERK_TABS
            ]
            phase["active"] = centers.index(target)
            draw_active()
        elif target == "navigation.goto_home":
            phase["state"] = "HOME_SCREEN"
        return True

    def close(_target, **_kwargs):
        phase["state"] = "CARDS"
        return True

    def top(*_args, screenshot, **_kwargs):
        return SimpleNamespace(success=True, screenshot=screenshot, reason="edge_reached")

    def scroll(*_args, screenshot, **_kwargs):
        return SimpleNamespace(
            success=True,
            screenshots=(screenshot,),
            reason="edge_reached",
        )

    result = capture_post_run_perk_configuration(
        perk_frame,
        battle_id="Battle 2026/07/22",
        evidence_root=tmp_path,
        capture_fn=capture,
        detector=detector,
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        safe_tap_fn=tap,
        tap_visible_fn=close,
        visible_fn=lambda *_args, **_kwargs: phase["state"] == "PERKS",
        scroll_top_fn=top,
        capture_scroll_fn=scroll,
        sleep_fn=lambda _seconds: None,
    )

    assert set(result.fields) == {
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    }
    assert result.home_screenshot is home
    for field in result.fields.values():
        assert field["quality"]["source_complete"] is True
        assert len(field["evidence_images"]) == 1
        assert (tmp_path / "Battle_2026_07_22" / "perk_configuration").is_dir()
