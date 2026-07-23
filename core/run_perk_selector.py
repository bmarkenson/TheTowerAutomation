"""Run-scoped manual Perk selection from a strict recorded whitelist."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.auto_pick_perks import measure_auto_pick_perks
from core.input import safe_tap, tap_if_visible
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]

NEW_PERK_REGION = (350, 25, 403, 71)
CHOOSE_PERK_REGION = (250, 320, 580, 85)
CHOICE_ROWS = (
    (405, 585),
    (600, 782),
    (796, 980),
    (992, 1178),
)
CHOICE_TEXT_X1 = 270
CHOICE_TEXT_X2 = 950
CHOICE_TAP_X = 540
MIN_CHOICE_CONFIDENCE = 65.0
TERMINAL_STATES = {"GAME_OVER", "TOURNAMENT_RESULTS"}


@dataclass(frozen=True)
class PerkChoice:
    display_text: str
    family: Optional[str]
    confidence: float
    top: int
    bottom: int


@dataclass(frozen=True)
class PerkChoicePanel:
    prompt_visible: bool
    auto_pick_valid: bool
    auto_pick_enabled: bool
    choices: tuple[PerkChoice, ...]


def canonical_perk_family(text: Any) -> Optional[str]:
    """Map a leveled Perk display string to a stable semantic family."""

    normalized = " ".join(
        re.findall(r"[a-z0-9.%+’-]+", str(text or "").casefold())
    )
    normalized = normalized.replace("’", "'")
    ordered_contains = (
        ("tower health regen", "tower_health_regen_tradeoff"),
        ("boss health", "boss_health_speed_tradeoff"),
        ("all coins bonuses", "all_coins_bonuses"),
        ("cash bonus", "cash_bonus"),
        ("land mine damage", "land_mine_damage"),
        ("free upgrade chance for all", "free_upgrade_chance"),
        ("golden tower", "golden_tower_bonus"),
        ("chain lightning", "chain_lightning_damage"),
        ("spotlight damage", "spotlight_damage_bonus"),
        ("defense percent", "defense_percent"),
        ("wave on death wave", "wave_on_death"),
        ("chrono field duration", "chrono_field_duration"),
        ("smart missiles", "smart_missiles"),
        ("perk wave requirement", "perk_wave_requirement"),
        ("inner mines", "inner_mines"),
        ("swamp radius", "swamp_radius"),
        ("black hole duration", "black_hole_duration"),
        ("max game speed", "max_game_speed"),
    )
    for fragment, family in ordered_contains:
        if fragment in normalized:
            return family
    if re.fullmatch(r"[|; ]*x?[0-9.]+ max health", normalized):
        return "max_health"
    if re.fullmatch(r"[|; ]*x?[0-9.]+ health regen", normalized):
        return "health_regen"
    if re.fullmatch(r"[|; ]*x?[0-9.]+ damage", normalized):
        return "damage"
    if re.fullmatch(r"[|; ]*orbs? \+[0-9]+", normalized):
        return "orbs"
    return None


def build_strict_perk_whitelist(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Derive only semantic families present in a completed battle record."""

    selected = record.get("perks", {}).get("selected", ())
    if not isinstance(selected, Sequence) or isinstance(selected, (str, bytes)):
        raise ValueError("battle record has no selected Perks list")
    families = []
    unknown = []
    for perk in selected:
        text = perk.get("display_text") if isinstance(perk, Mapping) else perk
        family = canonical_perk_family(text)
        if family is None:
            unknown.append(str(text))
        elif family not in families:
            families.append(family)
    if unknown:
        raise ValueError("unrecognized recorded Perks: " + "; ".join(unknown))
    if not families:
        raise ValueError("battle record selected Perks list is empty")
    return tuple(families)


def measure_new_perk_available(screenshot: Optional[Frame]) -> bool:
    """Recognize the localized in-battle ``New Perk`` progress label."""

    crop = _crop(screenshot, NEW_PERK_REGION)
    if crop is None:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    text, _confidence = ocr_text_and_conf(enlarged, psm=7)
    return "NEW PERK" in _normalize(text)


def inspect_perk_choice_panel(
    screenshot: Frame,
    *,
    detector: Detector = detect_state_and_overlays,
    text_fn: Callable[[Frame], tuple[str, float]] = lambda crop: ocr_text_and_conf(
        crop, psm=6
    ),
    header_text_fn: Callable[[Frame], tuple[str, float]] = lambda crop: (
        ocr_text_and_conf(crop, psm=7)
    ),
) -> PerkChoicePanel:
    """Read the four fixed choice rows without touching Auto Pick."""

    if detector(screenshot).get("state") != "PERKS":
        raise ValueError("Perks panel is not authoritatively visible")
    auto_pick = measure_auto_pick_perks(screenshot)
    header = _crop(screenshot, CHOOSE_PERK_REGION)
    header_text = "" if header is None else header_text_fn(header)[0]
    prompt_visible = "CHOOSE A NEW PERK" in _normalize(header_text)
    choices = []
    if prompt_visible:
        for top, bottom in CHOICE_ROWS:
            crop = screenshot[top:bottom, CHOICE_TEXT_X1:CHOICE_TEXT_X2]
            raw_text, confidence = text_fn(crop)
            display = " ".join(str(raw_text or "").split()).lstrip("|; ")
            if not display:
                continue
            choices.append(
                PerkChoice(
                    display,
                    canonical_perk_family(display),
                    float(confidence),
                    top,
                    bottom,
                )
            )
    return PerkChoicePanel(
        prompt_visible,
        auto_pick.valid_region,
        auto_pick.enabled,
        tuple(choices),
    )


class RunScopedPerkSelector:
    """Select recorded Perk families while one explicit runtime file is active."""

    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._next_check_at = 0.0
        self._route_open = False

    def handle(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        action_guard_fn: Callable[[], bool],
        capture_fn: Capture = capture_adb_screenshot,
        detector: Detector = detect_state_and_overlays,
        safe_tap_fn: Callable[..., bool] = safe_tap,
        tap_visible_fn: Callable[..., bool] = tap_if_visible,
        new_perk_fn: Callable[[Optional[Frame]], bool] = measure_new_perk_available,
        inspect_fn: Callable[[Frame], PerkChoicePanel] = inspect_perk_choice_panel,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Handle one pending choice route and force a caller recapture if opened."""

        config = self._load_active_config()
        now = self._clock()
        if config is not None and self._route_open:
            if detection.get("state") == "PERKS":
                if not action_guard_fn():
                    return False
                if tap_visible_fn(
                    "buttons.close:perks", screenshot=screenshot, retries=1
                ):
                    self._route_open = False
                    return True
                log(
                    "[PERK_SELECTOR] Could not close the selector-owned "
                    "Perks route; will retry",
                    "WARN",
                )
                self._next_check_at = now + 10.0
                return False
            self._route_open = False
        if (
            config is None
            or detection.get("state") != "RUNNING"
            or now < self._next_check_at
        ):
            return False
        self._next_check_at = now + 10.0
        if not new_perk_fn(screenshot):
            return False
        allowed = {
            str(value)
            for value in config.get("allowed_families", ())
            if str(value)
        }
        if not allowed:
            log("[PERK_SELECTOR] Active whitelist is empty; refusing input", "ERROR")
            self._next_check_at = now + 60.0
            return False
        if not action_guard_fn():
            return False
        if not safe_tap_fn(
            "navigation.open_perks",
            require_visible=False,
            dispatch="now",
            log_label="run_perk_selector:open",
        ):
            self._next_check_at = now + 30.0
            return False
        self._route_open = True
        current = screenshot
        selected_any = False
        try:
            current = _wait_for_perks(
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
            )
            for _ in range(8):
                panel = inspect_fn(current)
                if not panel.auto_pick_valid or panel.auto_pick_enabled:
                    log(
                        "[PERK_SELECTOR] Auto Pick is enabled or unreadable; "
                        "refusing manual selection",
                        "ERROR",
                        console=True,
                    )
                    self._next_check_at = now + 60.0
                    break
                if not panel.prompt_visible:
                    break
                eligible = [
                    choice
                    for choice in panel.choices
                    if choice.family in allowed
                    and choice.confidence >= MIN_CHOICE_CONFIDENCE
                ]
                if not eligible:
                    offered = ", ".join(
                        f"{choice.display_text} ({choice.family or 'unrecognized'})"
                        for choice in panel.choices
                    )
                    log(
                        "[PERK_SELECTOR] No offered Perk is in the strict "
                        f"whitelist; leaving the prompt unresolved: {offered}",
                        "WARN",
                        console=True,
                    )
                    self._next_check_at = now + 60.0
                    break
                chosen = eligible[0]
                if not action_guard_fn():
                    break
                fresh = capture_fn()
                fresh_panel = inspect_fn(fresh) if fresh is not None else None
                confirmed = _find_confirmed_choice(fresh_panel, chosen, allowed)
                if confirmed is None:
                    log(
                        "[PERK_SELECTOR] Choice changed before confirmation; "
                        "refusing the stale tap",
                        "WARN",
                    )
                    break
                if not safe_tap_fn(
                    (CHOICE_TAP_X, (confirmed.top + confirmed.bottom) // 2),
                    require_visible=False,
                    dispatch="now",
                    log_label=f"run_perk_selector:{confirmed.family}",
                ):
                    break
                selected_any = True
                self._record_selection(config, confirmed)
                log(
                    f"[PERK_SELECTOR] Selected {confirmed.display_text} "
                    f"from recorded family {confirmed.family}",
                    "INFO",
                    console=True,
                )
                sleep_fn(1.0)
                refreshed = capture_fn()
                if refreshed is None:
                    break
                current = refreshed
        except Exception as exc:
            log(
                f"[PERK_SELECTOR] Manual selection route failed safely: {exc}",
                "WARN",
                console=True,
            )
            self._next_check_at = now + 30.0
        finally:
            may_close = action_guard_fn()
            if may_close:
                if tap_visible_fn(
                    "buttons.close:perks", screenshot=current, retries=1
                ):
                    self._route_open = False
                else:
                    log(
                        "[PERK_SELECTOR] Could not verify Perks close control",
                        "WARN",
                    )
        self._next_check_at = self._clock() + (10.0 if selected_any else 60.0)
        return True

    def observe_state(self, state: str) -> None:
        """Deactivate the run-scoped file at a natural terminal boundary."""

        if state not in TERMINAL_STATES:
            return
        config = self._load_active_config()
        if config is None:
            return
        config["enabled"] = False
        config["completed_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        config["completion_reason"] = state
        self._write_config(config)
        log(f"[PERK_SELECTOR] Disabled strict whitelist at {state}", "INFO")

    def _load_active_config(self) -> Optional[dict[str, Any]]:
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(config, dict) or config.get("enabled") is not True:
            return None
        if config.get("schema_version") != 1 or config.get("strict") is not True:
            return None
        return config

    def _record_selection(self, config: dict[str, Any], choice: PerkChoice) -> None:
        selections = config.setdefault("selections", [])
        if isinstance(selections, list):
            selections.append(
                {
                    "selected_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "display_text": choice.display_text,
                    "family": choice.family,
                    "confidence": round(choice.confidence, 1),
                }
            )
            self._write_config(config)

    def _write_config(self, config: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _find_confirmed_choice(
    panel: Optional[PerkChoicePanel],
    chosen: PerkChoice,
    allowed: set[str],
) -> Optional[PerkChoice]:
    if panel is None or not panel.prompt_visible:
        return None
    if not panel.auto_pick_valid or panel.auto_pick_enabled:
        return None
    for choice in panel.choices:
        if (
            choice.family == chosen.family
            and choice.family in allowed
            and choice.confidence >= MIN_CHOICE_CONFIDENCE
            and choice.top == chosen.top
        ):
            return choice
    return None


def _wait_for_perks(*, capture_fn, detector, sleep_fn, attempts=12) -> Frame:
    for _ in range(attempts):
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "PERKS":
            return frame
        sleep_fn(0.25)
    raise ValueError("timed out waiting for Perks")


def _crop(frame: Optional[Frame], region: tuple[int, int, int, int]):
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
        return None
    x, y, width, height = region
    if y + height > frame.shape[0] or x + width > frame.shape[1]:
        return None
    return frame[y : y + height, x : x + width]


def _normalize(text: Any) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(text or "").upper()))


__all__ = [
    "PerkChoice",
    "PerkChoicePanel",
    "RunScopedPerkSelector",
    "build_strict_perk_whitelist",
    "canonical_perk_family",
    "inspect_perk_choice_panel",
    "measure_new_perk_available",
]
