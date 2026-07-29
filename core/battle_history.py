"""Guarded copied identity for the newest in-game Battle History entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.android_clipboard import ClipboardReadResult, read_battle_report_clipboard
from core.battle_lifecycle import HomeBattleControl
from core.gc_preflight_navigation import (
    _BattleEnded,
    _NavigationFailure,
    _ensure_running_side_menu_open,
    _guarded_visible_tap,
)
from core.home_battle import detect_home_battle_control
from core.input import TapVerification, safe_tap, tap_if_visible
from core.label_tapper import is_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
LATEST_HISTORY_ROW_REGION = (0, 280, 1080, 190)
HISTORY_DETAIL_IDENTITY_REGION = (120, 400, 840, 530)
_BATTLE_REPORT_FIELDS = (
    "battle_date",
    "game_time",
    "real_time",
    "tier",
    "wave",
    "killed_by",
    "coins_earned",
    "coins_per_hour",
    "cells_earned",
    "cells_per_hour",
)
_REQUIRED_IDENTITY_FIELDS = frozenset({"battle_date", "tier", "wave"})


class BattleHistoryReadStatus(str, Enum):
    COMPLETE = "complete"
    PAUSED = "paused"
    FAILED = "failed"
    BATTLE_ENDED = "battle_ended"


@dataclass(frozen=True)
class BattleHistoryIdentity:
    fingerprint: str
    battle_date: str
    tier: str
    wave: str
    fields: Mapping[str, str]

    def scope_metadata(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "fingerprint": self.fingerprint,
            "battle_date": self.battle_date,
            "tier": self.tier,
            "wave": self.wave,
            "captured_at": datetime.now().astimezone().isoformat(
                timespec="microseconds"
            ),
        }


@dataclass(frozen=True)
class BattleHistoryReadResult:
    status: BattleHistoryReadStatus
    reason: str
    identity: Optional[BattleHistoryIdentity] = None
    source_restored: bool = False

    @property
    def complete(self) -> bool:
        return (
            self.status is BattleHistoryReadStatus.COMPLETE
            and self.identity is not None
            and self.source_restored
        )


class _BattleHistoryPaused(RuntimeError):
    pass


def parse_battle_history_report(text: str) -> BattleHistoryIdentity:
    """Return a stable fingerprint from the copied Battle Report header."""

    normalized = (
        str(text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .rstrip("\x00")
    )
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "Battle Report":
        raise ValueError("clipboard does not begin with Battle Report")

    fields: dict[str, str] = {}
    for raw_line in lines[1:]:
        if not raw_line.strip():
            continue
        if "\t" not in raw_line:
            break
        label, value = (part.strip() for part in raw_line.split("\t", 1))
        key = _slug(label)
        if key in _BATTLE_REPORT_FIELDS and value:
            fields[key] = value

    missing = sorted(_REQUIRED_IDENTITY_FIELDS - fields.keys())
    if missing:
        raise ValueError(
            "Battle History report is missing identity fields: "
            + ", ".join(missing)
        )
    canonical = {
        key: fields[key]
        for key in _BATTLE_REPORT_FIELDS
        if key in fields
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return BattleHistoryIdentity(
        fingerprint=fingerprint,
        battle_date=fields["battle_date"],
        tier=fields["tier"],
        wave=fields["wave"],
        fields=canonical,
    )


def latest_history_row_visible(
    frame: Frame,
    *,
    detector: Detector = detect_state_and_overlays,
    is_visible_fn: Callable[..., bool] = is_visible,
    ocr_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> bool:
    """Verify that the first Battle History summary row is available."""

    detection = detector(frame)
    if str(detection.get("state") or "") != "BATTLE_HISTORY":
        return False
    if is_visible_fn("buttons.copy:more_stats", screenshot=frame):
        return False
    x, y, width, height = LATEST_HISTORY_ROW_REGION
    text, _confidence = ocr_fn(
        frame[y : y + height, x : x + width],
        psm=6,
    )
    normalized = re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()
    return "TIER " in f"{normalized} " and "WAVE " in f"{normalized} "


def history_detail_matches_identity(
    frame: Frame,
    identity: BattleHistoryIdentity,
    *,
    detector: Detector = detect_state_and_overlays,
    is_visible_fn: Callable[..., bool] = is_visible,
    ocr_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> bool:
    """Prove that unchanged clipboard text belongs to the visible detail."""

    detection = detector(frame)
    if str(detection.get("state") or "") != "BATTLE_HISTORY":
        return False
    if not is_visible_fn("buttons.copy:more_stats", screenshot=frame):
        return False
    x, y, width, height = HISTORY_DETAIL_IDENTITY_REGION
    text, _confidence = ocr_fn(
        frame[y : y + height, x : x + width],
        psm=6,
    )
    normalized = re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()
    return (
        f"TIER {identity.tier}" in normalized
        and f"WAVE {identity.wave}" in normalized
    )


def read_latest_completed_battle(
    *,
    source_state: str,
    expected_home_control: HomeBattleControl = HomeBattleControl.UNKNOWN,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    is_visible_fn: Callable[..., bool] = is_visible,
    clipboard_fn: Callable[[], ClipboardReadResult] = read_battle_report_clipboard,
    home_control_fn: Callable[[Frame], Any] = detect_home_battle_control,
    action_guard_fn: Callable[[], bool] = lambda: True,
    latest_row_visible_fn: Optional[Callable[[Frame], bool]] = None,
    detail_matches_fn: Optional[
        Callable[[Frame, BattleHistoryIdentity], bool]
    ] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BattleHistoryReadResult:
    """Copy the newest historical report and restore the source screen."""

    normalized_source = str(source_state or "").upper()
    if normalized_source not in {
        "RUNNING",
        "HOME_SCREEN",
        "BATTLE_HISTORY",
    }:
        return BattleHistoryReadResult(
            BattleHistoryReadStatus.FAILED,
            f"unsupported source state: {source_state}",
        )

    def require_action() -> None:
        if not action_guard_fn():
            raise _BattleHistoryPaused(
                "automation paused during Battle History inspection"
            )

    def guarded_capture() -> Optional[Frame]:
        require_action()
        return capture_fn()

    def guarded_visible_tap(*args, **kwargs) -> bool:
        require_action()
        return bool(tap_visible_fn(*args, **kwargs))

    def guarded_safe_tap(*args, **kwargs) -> bool:
        require_action()
        return bool(safe_tap_fn(*args, **kwargs))

    row_visible = latest_row_visible_fn or (
        lambda frame: latest_history_row_visible(
            frame,
            detector=detector,
            is_visible_fn=is_visible_fn,
        )
    )
    detail_matches = detail_matches_fn or (
        lambda frame, identity: history_detail_matches_identity(
            frame,
            identity,
            detector=detector,
            is_visible_fn=is_visible_fn,
        )
    )

    try:
        frame, detection = _capture_detection(guarded_capture, detector)
        state = str(detection.get("state") or "UNKNOWN")
        if state != "BATTLE_HISTORY":
            if normalized_source == "BATTLE_HISTORY":
                raise _NavigationFailure(
                    f"Battle History inspection resumed on {state}"
                )
            if normalized_source == "RUNNING":
                _ensure_running_side_menu_open(
                    capture_fn=guarded_capture,
                    detector=detector,
                    tap_visible_fn=guarded_visible_tap,
                    sleep_fn=sleep_fn,
                )
            else:
                if state != "HOME_SCREEN":
                    raise _NavigationFailure(
                        f"Home Battle History source changed to {state}"
                    )
                observed_control = home_control_fn(frame).control
                if (
                    expected_home_control is not HomeBattleControl.UNKNOWN
                    and observed_control is not expected_home_control
                ):
                    raise _NavigationFailure(
                        "Home battle control changed before History inspection"
                    )
            _guarded_visible_tap(
                (
                    "navigation.battle_history_running"
                    if normalized_source == "RUNNING"
                    else "navigation.battle_history_home"
                ),
                allowed_states={normalized_source},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            frame = _wait_for_history(
                detail=False,
                capture_fn=guarded_capture,
                detector=detector,
                is_visible_fn=is_visible_fn,
                sleep_fn=sleep_fn,
            )

        if (
            normalized_source == "BATTLE_HISTORY"
            and is_visible_fn("buttons.copy:more_stats", screenshot=frame)
        ):
            _guarded_visible_tap(
                "buttons.close:more_stats",
                allowed_states={"BATTLE_HISTORY"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            frame = _wait_for_history(
                detail=False,
                capture_fn=guarded_capture,
                detector=detector,
                is_visible_fn=is_visible_fn,
                sleep_fn=sleep_fn,
            )

        if not is_visible_fn("buttons.copy:more_stats", screenshot=frame):
            if not row_visible(frame):
                raise _NavigationFailure(
                    "latest Battle History row was not verified"
                )
            if not guarded_safe_tap(
                "buttons.battle_history_latest",
                dispatch="now",
                verification=TapVerification(
                    screenshot=frame,
                    target_region=LATEST_HISTORY_ROW_REGION,
                    description="battle_history:latest_completed_row",
                    verifier=row_visible,
                ),
            ):
                raise _NavigationFailure(
                    "latest Battle History row tap failed"
                )
            frame = _wait_for_history(
                detail=True,
                capture_fn=guarded_capture,
                detector=detector,
                is_visible_fn=is_visible_fn,
                sleep_fn=sleep_fn,
            )

        before = clipboard_fn()
        _guarded_visible_tap(
            "buttons.copy:more_stats",
            allowed_states={"BATTLE_HISTORY"},
            capture_fn=guarded_capture,
            detector=detector,
            tap_visible_fn=guarded_visible_tap,
            sleep_fn=sleep_fn,
        )
        identity = None
        parse_reason = "clipboard read failed"
        copied_text = None
        for attempt in range(4):
            if attempt:
                sleep_fn(0.25)
            candidate = clipboard_fn()
            if not candidate.success or not candidate.text:
                parse_reason = candidate.reason
                continue
            try:
                parsed = parse_battle_history_report(candidate.text)
            except ValueError as exc:
                parse_reason = str(exc)
                continue
            copied_text = candidate.text
            identity = parsed
            break
        if identity is None or copied_text is None:
            raise _NavigationFailure(parse_reason)
        if (
            before.success
            and copied_text == before.text
            and not detail_matches(frame, identity)
        ):
            raise _NavigationFailure(
                "unchanged clipboard could not be matched to visible History detail"
            )

        _guarded_visible_tap(
            "buttons.close:more_stats",
            allowed_states={"BATTLE_HISTORY"},
            capture_fn=guarded_capture,
            detector=detector,
            tap_visible_fn=guarded_visible_tap,
            sleep_fn=sleep_fn,
        )
        _wait_for_history(
            detail=False,
            capture_fn=guarded_capture,
            detector=detector,
            is_visible_fn=is_visible_fn,
            sleep_fn=sleep_fn,
        )
        _restore_source(
            normalized_source,
            expected_home_control=expected_home_control,
            capture_fn=guarded_capture,
            detector=detector,
            tap_visible_fn=guarded_visible_tap,
            home_control_fn=home_control_fn,
            sleep_fn=sleep_fn,
        )
        return BattleHistoryReadResult(
            BattleHistoryReadStatus.COMPLETE,
            "latest completed battle copied",
            identity=identity,
            source_restored=True,
        )
    except _BattleHistoryPaused as exc:
        return BattleHistoryReadResult(
            BattleHistoryReadStatus.PAUSED,
            str(exc),
        )
    except _BattleEnded as exc:
        return BattleHistoryReadResult(
            BattleHistoryReadStatus.BATTLE_ENDED,
            str(exc),
        )
    except (_NavigationFailure, ValueError) as exc:
        restored = _restore_source_best_effort(
            normalized_source,
            expected_home_control=expected_home_control,
            capture_fn=guarded_capture,
            detector=detector,
            tap_visible_fn=guarded_visible_tap,
            is_visible_fn=is_visible_fn,
            home_control_fn=home_control_fn,
            sleep_fn=sleep_fn,
        )
        return BattleHistoryReadResult(
            BattleHistoryReadStatus.FAILED,
            str(exc),
            source_restored=restored,
        )


def _capture_detection(
    capture_fn: Capture,
    detector: Detector,
) -> tuple[Frame, Mapping[str, Any]]:
    frame = capture_fn()
    if frame is None:
        raise _NavigationFailure("screenshot capture failed")
    detection = detector(frame)
    if detection.get("state") in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
        raise _BattleEnded(
            "battle ended during Battle History continuity inspection"
        )
    return frame, detection


def _wait_for_history(
    *,
    detail: bool,
    capture_fn: Capture,
    detector: Detector,
    is_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    for attempt in range(24):
        frame, detection = _capture_detection(capture_fn, detector)
        copy_visible = is_visible_fn(
            "buttons.copy:more_stats",
            screenshot=frame,
        )
        if (
            str(detection.get("state") or "") == "BATTLE_HISTORY"
            and copy_visible is detail
        ):
            return frame
        if attempt < 23:
            sleep_fn(0.35)
    raise _NavigationFailure(
        "timed out waiting for Battle History "
        + ("detail" if detail else "list")
    )


def _restore_source(
    source_state: str,
    *,
    expected_home_control: HomeBattleControl,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    home_control_fn: Callable[[Frame], Any],
    sleep_fn: Callable[[float], None],
) -> None:
    _guarded_visible_tap(
        "buttons.return_to_game",
        allowed_states={"BATTLE_HISTORY"},
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    expected_states = (
        {"RUNNING", "HOME_SCREEN"}
        if source_state == "BATTLE_HISTORY"
        else {source_state}
    )
    for attempt in range(24):
        frame, detection = _capture_detection(capture_fn, detector)
        observed_state = str(detection.get("state") or "")
        if observed_state in expected_states:
            if observed_state == "HOME_SCREEN":
                observed_control = home_control_fn(frame).control
                if (
                    expected_home_control is not HomeBattleControl.UNKNOWN
                    and observed_control is not expected_home_control
                ):
                    raise _BattleEnded(
                        "Home battle control changed during History inspection"
                    )
            return
        if attempt < 23:
            sleep_fn(0.35)
    raise _NavigationFailure(
        "Battle History did not restore source state="
        + "|".join(sorted(expected_states))
    )


def _restore_source_best_effort(
    source_state: str,
    *,
    expected_home_control: HomeBattleControl,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    is_visible_fn: Callable[..., bool],
    home_control_fn: Callable[[Frame], Any],
    sleep_fn: Callable[[float], None],
) -> bool:
    try:
        frame, detection = _capture_detection(capture_fn, detector)
        state = str(detection.get("state") or "UNKNOWN")
        if state == source_state and source_state != "BATTLE_HISTORY":
            return True
        if (
            source_state == "BATTLE_HISTORY"
            and state in {"RUNNING", "HOME_SCREEN"}
        ):
            return True
        if state != "BATTLE_HISTORY":
            return False
        if is_visible_fn("buttons.copy:more_stats", screenshot=frame):
            _guarded_visible_tap(
                "buttons.close:more_stats",
                allowed_states={"BATTLE_HISTORY"},
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            _wait_for_history(
                detail=False,
                capture_fn=capture_fn,
                detector=detector,
                is_visible_fn=is_visible_fn,
                sleep_fn=sleep_fn,
            )
        _restore_source(
            source_state,
            expected_home_control=expected_home_control,
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            home_control_fn=home_control_fn,
            sleep_fn=sleep_fn,
        )
        return True
    except (
        _BattleEnded,
        _BattleHistoryPaused,
        _NavigationFailure,
        ValueError,
    ):
        return False


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


__all__ = [
    "BattleHistoryIdentity",
    "BattleHistoryReadResult",
    "BattleHistoryReadStatus",
    "history_detail_matches_identity",
    "latest_history_row_visible",
    "parse_battle_history_report",
    "read_latest_completed_battle",
]
