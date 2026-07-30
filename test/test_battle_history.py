from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from core.android_clipboard import ClipboardReadResult
from core.battle_lifecycle import HomeBattleControl
from core.battle_history import (
    BattleHistoryReadStatus,
    history_detail_matches_identity,
    latest_history_row_visible,
    parse_battle_history_report,
    read_latest_completed_battle,
)
from core.matcher import get_match_result


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"
HISTORY_REPORT = (
    FIXTURES / "battle_history_report_clipboard.txt"
).read_text(encoding="utf-8")
FRAME = np.full((1920, 1080, 3), 64, dtype=np.uint8)


def test_battle_history_report_fingerprint_uses_stable_header_identity():
    unix = parse_battle_history_report(HISTORY_REPORT)
    windows = parse_battle_history_report(
        HISTORY_REPORT.replace("\n", "\r\n")
    )

    assert unix == windows
    assert unix.battle_date == "Jul 15, 2026 01:41"
    assert unix.tier == "18"
    assert unix.wave == "9112"
    assert len(unix.fingerprint) == 64


def test_retained_history_frames_verify_latest_row_and_copied_detail():
    history = cv2.imread(
        str(FIXTURES / "ui_state_20260714" / "active_battle_history.png")
    )
    detail = cv2.imread(
        str(
            FIXTURES
            / "ui_state_20260714"
            / "active_battle_history_detail.png"
        )
    )
    identity = parse_battle_history_report(HISTORY_REPORT)

    assert latest_history_row_visible(history)
    assert history_detail_matches_identity(detail, identity)


def test_retained_source_frames_match_battle_history_navigation():
    home = cv2.imread(
        str(FIXTURES / "home_screen_no_reward_badges_20260714.png")
    )
    running_menu = cv2.imread(
        str(
            FIXTURES / "running_menu_no_reward_badges_20260715.png"
        )
    )

    assert get_match_result(
        "navigation.battle_history_home",
        screenshot=home,
    ).matched
    assert get_match_result(
        "navigation.battle_history_running",
        screenshot=running_menu,
    ).matched


def test_home_history_navigation_allows_an_extra_right_rail_control():
    home = cv2.imread(
        str(FIXTURES / "home_screen_eight_nav_controls_20260729.png")
    )

    result = get_match_result(
        "navigation.battle_history_home",
        screenshot=home,
    )

    assert result.matched
    assert result.center == (1021, 909)


class _FakeHistoryUi:
    def __init__(self, *, source_state="RUNNING", initial_state=None):
        self.source_state = source_state
        self.state = initial_state or source_state
        self.menu_open = False
        self.clipboard = HISTORY_REPORT
        self.inputs = []
        self.pause_after_open = False
        self.actions_allowed = True

    def capture(self):
        return FRAME.copy()

    def detect(self, _frame):
        if self.state == "RUNNING":
            overlays = ["MENU_OPEN" if self.menu_open else "MENU_CLOSED"]
            return {"state": "RUNNING", "overlays": overlays}
        if self.state in {"BATTLE_HISTORY", "BATTLE_HISTORY_DETAIL"}:
            return {"state": "BATTLE_HISTORY", "overlays": []}
        return {"state": self.state, "overlays": []}

    def visible(self, key, *, screenshot=None):
        del screenshot
        if key == "buttons.copy:more_stats":
            return self.state == "BATTLE_HISTORY_DETAIL"
        return True

    def tap_visible(self, key, **_kwargs):
        self.inputs.append(key)
        if key == "navigation.menu_open_button":
            self.menu_open = True
        elif key == "navigation.battle_history_running":
            self.state = "BATTLE_HISTORY"
            if self.pause_after_open:
                self.actions_allowed = False
        elif key == "navigation.battle_history_home":
            self.state = "BATTLE_HISTORY"
        elif key == "buttons.copy:more_stats":
            self.clipboard = HISTORY_REPORT
        elif key == "buttons.close:more_stats":
            self.state = "BATTLE_HISTORY"
        elif key == "buttons.return_to_game":
            self.state = self.source_state
            self.menu_open = False
        return True

    def safe_tap(self, key, *, verification, **_kwargs):
        assert key == "buttons.battle_history_latest"
        assert verification.authorizes((540, 370))
        self.inputs.append(key)
        self.state = "BATTLE_HISTORY_DETAIL"
        return True

    def clipboard_read(self):
        return ClipboardReadResult(self.clipboard, "battle_report")


def _read(ui, *, source_state="RUNNING", expected_home_control=None):
    if expected_home_control is None:
        expected_home_control = HomeBattleControl.UNKNOWN
    return read_latest_completed_battle(
        source_state=source_state,
        expected_home_control=expected_home_control,
        capture_fn=ui.capture,
        detector=ui.detect,
        safe_tap_fn=ui.safe_tap,
        tap_visible_fn=ui.tap_visible,
        is_visible_fn=ui.visible,
        clipboard_fn=ui.clipboard_read,
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        action_guard_fn=lambda: ui.actions_allowed,
        latest_row_visible_fn=lambda _frame: True,
        detail_matches_fn=lambda _frame, _identity: True,
        sleep_fn=lambda _seconds: None,
    )


def test_history_reader_copies_latest_detail_and_restores_running():
    ui = _FakeHistoryUi()

    result = _read(ui)

    assert result.complete
    assert result.identity is not None
    assert result.identity.wave == "9112"
    assert ui.state == "RUNNING"
    assert ui.inputs == [
        "navigation.menu_open_button",
        "navigation.battle_history_running",
        "buttons.battle_history_latest",
        "buttons.copy:more_stats",
        "buttons.close:more_stats",
        "buttons.return_to_game",
    ]


def test_history_reader_copies_latest_detail_and_restores_home():
    ui = _FakeHistoryUi(source_state="HOME_SCREEN")

    result = _read(
        ui,
        source_state="HOME_SCREEN",
        expected_home_control=HomeBattleControl.NEW_BATTLE,
    )

    assert result.complete
    assert result.identity is not None
    assert result.identity.wave == "9112"
    assert ui.state == "HOME_SCREEN"
    assert ui.inputs == [
        "navigation.battle_history_home",
        "buttons.battle_history_latest",
        "buttons.copy:more_stats",
        "buttons.close:more_stats",
        "buttons.return_to_game",
    ]


def test_history_reader_recovers_an_interrupted_detail_before_copying_latest():
    ui = _FakeHistoryUi(
        source_state="RUNNING",
        initial_state="BATTLE_HISTORY_DETAIL",
    )

    result = _read(ui, source_state="BATTLE_HISTORY")

    assert result.complete
    assert ui.state == "RUNNING"
    assert ui.inputs == [
        "buttons.close:more_stats",
        "buttons.battle_history_latest",
        "buttons.copy:more_stats",
        "buttons.close:more_stats",
        "buttons.return_to_game",
    ]


def test_history_reader_resumes_after_pause_without_sending_cleanup_input():
    ui = _FakeHistoryUi()
    ui.pause_after_open = True

    paused = _read(ui)

    assert paused.status is BattleHistoryReadStatus.PAUSED
    assert ui.state == "BATTLE_HISTORY"
    assert ui.inputs == [
        "navigation.menu_open_button",
        "navigation.battle_history_running",
    ]

    ui.actions_allowed = True
    ui.pause_after_open = False
    resumed = _read(ui)

    assert resumed.complete
    assert ui.state == "RUNNING"
    assert ui.inputs[-4:] == [
        "buttons.battle_history_latest",
        "buttons.copy:more_stats",
        "buttons.close:more_stats",
        "buttons.return_to_game",
    ]
