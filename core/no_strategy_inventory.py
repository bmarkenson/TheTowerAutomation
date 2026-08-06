"""Guarded, read-only in-battle inventory for ``No Strategy`` runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.damage_adjuster import dismiss_damage_adjuster, open_damage_adjuster
from core.gc_preflight_navigation import (
    _BattleEnded,
    _NavigationFailure,
    _ensure_event_bots_top,
    _ensure_running_side_menu_open,
    _guarded_static_tap,
    _guarded_visible_tap,
    _return_to_game_from_section,
    _select_running_menu,
    _wait_for,
)
from core.input import safe_tap, swipe_now, tap_if_visible
from core.no_strategy_observer import detect_attack_dissonance_badge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from core.upgrade_navigation import swipe_upgrade_menu


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

RECOVERABLE_INVENTORY_STATES = frozenset(
    {
        "RUNNING",
        "CARDS",
        "PERKS",
        "MODULES",
        "EVENT",
        "GUILD",
        "TARGET_PRIORITY",
        "DAMAGE_ADJUSTER",
    }
)
IN_BATTLE_INVENTORY_FIELDS = frozenset(
    {
        "cards_deck",
        "bots_preset",
        "guardian_chips",
        "modules",
        "target_priority",
        "damage_slider",
        "auto_pick_perks",
        "ultimate_weapons",
    }
)


class NoStrategyInventoryStatus(str, Enum):
    COMPLETE = "complete"
    PAUSED = "paused"
    FAILED = "failed"
    BATTLE_ENDED = "battle_ended"


@dataclass(frozen=True)
class NoStrategyInventoryResult:
    status: NoStrategyInventoryStatus
    reason: str

    @property
    def complete(self) -> bool:
        return self.status is NoStrategyInventoryStatus.COMPLETE


class _InventoryPaused(RuntimeError):
    pass


def run_no_strategy_in_battle_inventory(
    observer: Any,
    *,
    capture_fn: Capture = capture_adb_screenshot,
    detector: Detector = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    tap_visible_fn: Callable[..., bool] = tap_if_visible,
    swipe_fn: Callable[[str, str], Any] = swipe_upgrade_menu,
    event_swipe_fn: Callable[[str], bool] = swipe_now,
    control_sync: Callable[[], Any] = lambda: None,
    actions_allowed: Callable[[], bool] = lambda: True,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> NoStrategyInventoryResult:
    """Visit safe configuration screens, record them, and restore the battle.

    Every input is preceded by a control synchronization and fresh screen
    guard.  If Pause arrives mid-pass, no cleanup tap is sent; a later resumed
    pass first restores the known read-only screen to ``RUNNING``.
    """

    def require_action() -> None:
        control_sync()
        if not actions_allowed():
            raise _InventoryPaused("automation paused during inventory")

    def guarded_capture() -> Optional[Frame]:
        require_action()
        return capture_fn()

    def guarded_safe_tap(*args, **kwargs) -> bool:
        require_action()
        return bool(safe_tap_fn(*args, **kwargs))

    def guarded_visible_tap(*args, **kwargs) -> bool:
        require_action()
        return bool(tap_visible_fn(*args, **kwargs))

    def guarded_swipe(direction: str, span: str) -> Any:
        require_action()
        return swipe_fn(direction, span)

    def guarded_event_swipe(label: str) -> bool:
        require_action()
        return bool(event_swipe_fn(label))

    def observe(frame: Frame) -> Mapping[str, Any]:
        detection = detector(frame)
        observer.observe(frame, detection, phase="in_battle")
        return detection

    def unresolved_inventory_fields() -> set[str]:
        unresolved_fn = getattr(observer, "unresolved_fields", None)
        if callable(unresolved_fn):
            try:
                unresolved = unresolved_fn(IN_BATTLE_INVENTORY_FIELDS)
            except (TypeError, ValueError):
                unresolved = None
            if isinstance(unresolved, (set, frozenset, list, tuple)):
                return set(unresolved).intersection(IN_BATTLE_INVENTORY_FIELDS)
        # Compatibility for observer doubles and older restored snapshots: a
        # missing planning API must preserve the established full traversal.
        return set(IN_BATTLE_INVENTORY_FIELDS)

    route_completed = False
    try:
        initial = _restore_running_view(
            capture_fn=guarded_capture,
            detector=detector,
            safe_tap_fn=guarded_safe_tap,
            tap_visible_fn=guarded_visible_tap,
            sleep_fn=sleep_fn,
        )
        observe(initial)
        attack_dissonance = detect_attack_dissonance_badge(initial)["observed"]
        unresolved = unresolved_inventory_fields()
        planned_fields = tuple(sorted(unresolved))

        if "cards_deck" in unresolved:
            _ensure_running_side_menu_open(
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.Cards",
                allowed_states={"RUNNING"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            cards = _wait_for(
                state="CARDS",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            observe(cards)
            _return_to_game_from_section(
                state="CARDS",
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )

        if "auto_pick_perks" in unresolved:
            _guarded_static_tap(
                "navigation.open_perks",
                allowed_states={"RUNNING"},
                capture_fn=guarded_capture,
                detector=detector,
                safe_tap_fn=guarded_safe_tap,
            )
            perks = _wait_for(
                state="PERKS",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            observe(perks)
            _guarded_visible_tap(
                "buttons.close:perks",
                allowed_states={"PERKS"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _wait_for(
                state="RUNNING",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )

        if "ultimate_weapons" in unresolved:
            _select_running_menu(
                "navigation.goto_uw",
                "UW_MENU",
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            for _ in range(3):
                frame = _wait_for(
                    state="RUNNING",
                    menu="UW_MENU",
                    capture_fn=guarded_capture,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
                observe(frame)
                guarded_swipe("towards_top", "extended")
                sleep_fn(0.5)
            for position in range(6):
                frame = _wait_for(
                    state="RUNNING",
                    menu="UW_MENU",
                    capture_fn=guarded_capture,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )
                observe(frame)
                if position < 5:
                    guarded_swipe("towards_bottom", "medium")
                    sleep_fn(0.5)

        if "modules" in unresolved:
            _capture_section(
                observer,
                open_key="navigation.menu_modules",
                state="MODULES",
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )

        if "bots_preset" in unresolved:
            _ensure_running_side_menu_open(
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.menu_event",
                allowed_states={"RUNNING"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _wait_for(
                state="EVENT",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.event:bots_tab",
                allowed_states={"EVENT"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            bots = _wait_for(
                state="EVENT",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            bots = _ensure_event_bots_top(
                bots,
                capture_fn=guarded_capture,
                detector=detector,
                event_swipe_fn=guarded_event_swipe,
                sleep_fn=sleep_fn,
            )
            observe(bots)
            _return_to_game_from_section(
                state="EVENT",
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )

        if "guardian_chips" in unresolved:
            _ensure_running_side_menu_open(
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.menu_guild",
                allowed_states={"RUNNING"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _wait_for(
                state="GUILD",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            _guarded_visible_tap(
                "navigation.guild:guardian_tab",
                allowed_states={"GUILD"},
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            guardians = _wait_for(
                state="GUILD",
                secondary="GUILD_GUARDIAN_SCREEN",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            observe(guardians)
            _return_to_game_from_section(
                state="GUILD",
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )

        if "target_priority" in unresolved:
            _ensure_running_side_menu_open(
                capture_fn=guarded_capture,
                detector=detector,
                tap_visible_fn=guarded_visible_tap,
                sleep_fn=sleep_fn,
            )
            _guarded_static_tap(
                "navigation.target_priority",
                allowed_states={"RUNNING"},
                capture_fn=guarded_capture,
                detector=detector,
                safe_tap_fn=guarded_safe_tap,
            )
            target_priority = _wait_for(
                state="TARGET_PRIORITY",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            observe(target_priority)
            _guarded_static_tap(
                "buttons.close:target_priority",
                allowed_states={"TARGET_PRIORITY"},
                capture_fn=guarded_capture,
                detector=detector,
                safe_tap_fn=guarded_safe_tap,
            )
            _wait_for(
                state="RUNNING",
                capture_fn=guarded_capture,
                detector=detector,
                sleep_fn=sleep_fn,
            )

        if "damage_slider" in unresolved:
            if attack_dissonance:
                observer.record_unavailable(
                    "damage_slider",
                    reason="Attack menu disabled by Attack Dissonance",
                    source="attack_dissonance_menu_constraint",
                    phase="in_battle",
                )
            else:
                _capture_damage_slider(
                    observer,
                    capture_fn=guarded_capture,
                    detector=detector,
                    safe_tap_fn=guarded_safe_tap,
                    tap_visible_fn=guarded_visible_tap,
                    swipe_fn=guarded_swipe,
                    sleep_fn=sleep_fn,
                )

        _wait_for(
            state="RUNNING",
            capture_fn=guarded_capture,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        route_completed = True
        return NoStrategyInventoryResult(
            NoStrategyInventoryStatus.COMPLETE,
            (
                "all in-battle fields were already resolved without UI navigation"
                if not planned_fields
                else "visited only the remaining UI fields: "
                + ", ".join(planned_fields)
            ),
        )
    except _InventoryPaused as exc:
        return NoStrategyInventoryResult(NoStrategyInventoryStatus.PAUSED, str(exc))
    except _BattleEnded as exc:
        return NoStrategyInventoryResult(
            NoStrategyInventoryStatus.BATTLE_ENDED,
            str(exc),
        )
    except Exception as exc:
        return NoStrategyInventoryResult(NoStrategyInventoryStatus.FAILED, str(exc))
    finally:
        if not route_completed:
            try:
                control_sync()
                if actions_allowed():
                    _restore_running_view(
                        capture_fn=guarded_capture,
                        detector=detector,
                        safe_tap_fn=guarded_safe_tap,
                        tap_visible_fn=guarded_visible_tap,
                        sleep_fn=sleep_fn,
                    )
            except Exception:
                pass


def _capture_section(
    observer: Any,
    *,
    open_key: str,
    state: str,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> None:
    _ensure_running_side_menu_open(
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    _guarded_visible_tap(
        open_key,
        allowed_states={"RUNNING"},
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )
    frame = _wait_for(
        state=state,
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )
    observer.observe(frame, detector(frame), phase="in_battle")
    _return_to_game_from_section(
        state=state,
        capture_fn=capture_fn,
        detector=detector,
        tap_visible_fn=tap_visible_fn,
        sleep_fn=sleep_fn,
    )


def _capture_damage_slider(
    observer: Any,
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    tap_visible_fn: Callable[..., bool],
    swipe_fn: Callable[[str, str], Any],
    sleep_fn: Callable[[float], None],
) -> None:
    try:
        _select_running_menu(
            "navigation.goto_attack",
            "ATTACK_MENU",
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
        reading = open_damage_adjuster(
            capture_fn=capture_fn,
            tap_visible_fn=tap_visible_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
        )
        if reading is None or not reading.visible:
            raise _NavigationFailure("Damage adjuster did not open")
        frame = _wait_for(
            state="DAMAGE_ADJUSTER",
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
        )
        observer.observe(frame, detector(frame), phase="in_battle")
    except _InventoryPaused:
        raise
    except Exception as exc:
        observer.record_unavailable(
            "damage_slider",
            reason=str(exc),
            source="damage_adjuster_navigation",
            phase="in_battle",
        )
    finally:
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "DAMAGE_ADJUSTER":
            dismiss_damage_adjuster(
                capture_fn=capture_fn,
                tap_fn=safe_tap_fn,
                sleep_fn=sleep_fn,
            )


def _restore_running_view(
    *,
    capture_fn: Capture,
    detector: Detector,
    safe_tap_fn: Callable[..., bool],
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
) -> Frame:
    frame = capture_fn()
    if frame is None:
        raise _NavigationFailure("screenshot capture failed")
    detection = detector(frame)
    state = str(detection.get("state") or "UNKNOWN")
    if state in {"GAME_OVER", "TOURNAMENT_RESULTS"}:
        raise _BattleEnded("natural terminal result observed during inventory")
    if state == "RUNNING":
        return frame
    if state in {"CARDS", "MODULES", "EVENT", "GUILD"}:
        _guarded_visible_tap(
            "buttons.return_to_game",
            allowed_states={state},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
    elif state == "PERKS":
        _guarded_visible_tap(
            "buttons.close:perks",
            allowed_states={"PERKS"},
            capture_fn=capture_fn,
            detector=detector,
            tap_visible_fn=tap_visible_fn,
            sleep_fn=sleep_fn,
        )
    elif state == "TARGET_PRIORITY":
        _guarded_static_tap(
            "buttons.close:target_priority",
            allowed_states={"TARGET_PRIORITY"},
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
        )
    elif state == "DAMAGE_ADJUSTER":
        if not dismiss_damage_adjuster(
            capture_fn=capture_fn,
            tap_fn=safe_tap_fn,
            sleep_fn=sleep_fn,
        ):
            raise _NavigationFailure("could not dismiss Damage adjuster")
    else:
        raise _NavigationFailure(f"cannot restore inventory from state={state}")
    return _wait_for(
        state="RUNNING",
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
    )


__all__ = [
    "IN_BATTLE_INVENTORY_FIELDS",
    "NoStrategyInventoryResult",
    "NoStrategyInventoryStatus",
    "RECOVERABLE_INVENTORY_STATES",
    "run_no_strategy_in_battle_inventory",
]
