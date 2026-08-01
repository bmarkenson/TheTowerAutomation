"""GC module-loadout validation and guarded no-battle correction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
import time
from typing import Any, Callable, Mapping, Optional

import cv2
import numpy as np

from core.input import TapVerification, safe_tap, swipe_now
from core.module_icon_index import (
    EquippedModuleMatch,
    ModuleIconCatalog,
    ancestral_green_fraction,
    identify_equipped_ancestral_modules,
    load_module_icon_catalog,
    rank_module_icon_candidates,
)
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import log
from utils.ocr_utils import ocr_text_and_conf


INVENTORY_COLUMNS = (145, 345, 543, 741, 941)
INVENTORY_ROWS = (1090, 1295, 1500)
INVENTORY_ICON_CROP_SIZE = 134
INVENTORY_FRAME_CROP_SIZE = 190
MODULE_DETAIL_SETTLE_SECONDS = 1.0
_MAX_CORRECTION_STEPS = 16
_MAX_INVENTORY_SCROLLS = 8


@dataclass(frozen=True)
class GcModuleSlotEvidence:
    slot_key: str
    family: str
    role: str
    expected: str
    actual: Optional[str]
    match_status: str
    valid: bool
    confidence: float
    margin: float
    green_fraction: float


@dataclass(frozen=True)
class GcModuleLoadoutEvidence:
    slots: tuple[GcModuleSlotEvidence, ...]

    @property
    def valid(self) -> bool:
        return bool(self.slots) and all(slot.valid for slot in self.slots)

    @property
    def has_authoritative_mismatch(self) -> bool:
        """Whether every invalid slot names a confidently matched wrong module."""

        invalid = [slot for slot in self.slots if not slot.valid]
        return bool(invalid) and all(
            slot.match_status == "matched" and slot.actual is not None
            for slot in invalid
        )

    @property
    def fully_observed(self) -> bool:
        """Whether every equipped slot has authoritative identity evidence."""

        return bool(self.slots) and all(
            slot.match_status == "matched" and slot.actual is not None
            for slot in self.slots
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["valid"] = self.valid
        payload["fully_observed"] = self.fully_observed
        return payload


@dataclass(frozen=True)
class ModuleDetailEvidence:
    name: str
    rarity: str
    equipped: str
    action: str
    level: Optional[int]


class ModuleLoadoutCorrectionError(RuntimeError):
    pass


class ModuleInventorySelectionError(ModuleLoadoutCorrectionError):
    """The requested inventory module was not authoritatively available."""


def gc_module_loadout_evidence_from_dict(
    raw: Mapping[str, Any],
) -> GcModuleLoadoutEvidence:
    """Rehydrate retained no-battle module evidence for session reporting."""

    if not isinstance(raw, Mapping):
        raise ValueError("module evidence must be a mapping")
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, (list, tuple)):
        raise ValueError("module evidence must contain slots")
    slots = []
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, Mapping):
            raise ValueError("module slot evidence must be a mapping")
        slots.append(
            GcModuleSlotEvidence(
                slot_key=str(raw_slot.get("slot_key") or ""),
                family=str(raw_slot.get("family") or ""),
                role=str(raw_slot.get("role") or ""),
                expected=str(raw_slot.get("expected") or ""),
                actual=(
                    str(raw_slot["actual"])
                    if raw_slot.get("actual") is not None
                    else None
                ),
                match_status=str(raw_slot.get("match_status") or ""),
                valid=bool(raw_slot.get("valid")),
                confidence=float(raw_slot.get("confidence") or 0.0),
                margin=float(raw_slot.get("margin") or 0.0),
                green_fraction=float(raw_slot.get("green_fraction") or 0.0),
            )
        )
    return GcModuleLoadoutEvidence(tuple(slots))


def normalize_gc_module_requirements(
    raw: Any,
    *,
    catalog: Optional[ModuleIconCatalog] = None,
) -> dict[str, str]:
    """Validate an exact eight-slot Ancestral module requirement mapping."""

    if not isinstance(raw, Mapping):
        raise ValueError("gc_farm session_preflight.modules must be a mapping")
    selected_catalog = catalog or load_module_icon_catalog()
    slots = {slot.key: slot for slot in selected_catalog.slots}
    supplied = {str(key).strip(): str(value).strip() for key, value in raw.items()}
    if set(supplied) != set(slots):
        missing = sorted(set(slots) - set(supplied))
        extra = sorted(set(supplied) - set(slots))
        raise ValueError(
            "gc_farm session_preflight.modules must define every equipped slot "
            f"exactly once (missing={missing}, extra={extra})"
        )

    modules = {module.name: module for module in selected_catalog.modules}
    if len(set(supplied.values())) != len(supplied):
        raise ValueError("gc_farm session_preflight.modules cannot repeat a module")
    for key, expected in supplied.items():
        module = modules.get(expected)
        if module is None:
            raise ValueError(f"unknown Ancestral module requirement: {expected!r}")
        if module.family != slots[key].family:
            raise ValueError(
                f"module {expected!r} is {module.family}, not {slots[key].family} "
                f"for slot {key!r}"
            )
    return {slot.key: supplied[slot.key] for slot in selected_catalog.slots}


def evaluate_gc_module_loadout(
    screen,
    requirements: Mapping[str, Any],
    *,
    identify_fn: Callable[..., tuple[EquippedModuleMatch, ...]] = (
        identify_equipped_ancestral_modules
    ),
    catalog: Optional[ModuleIconCatalog] = None,
) -> GcModuleLoadoutEvidence:
    """Compare one Modules overview with an exact profile requirement."""

    selected_catalog = catalog or load_module_icon_catalog()
    expected = normalize_gc_module_requirements(
        requirements,
        catalog=selected_catalog,
    )
    matches = {
        match.slot_key: match
        for match in identify_fn(screen, catalog=selected_catalog)
    }
    slots: list[GcModuleSlotEvidence] = []
    for slot in selected_catalog.slots:
        match = matches.get(slot.key)
        if match is None:
            slots.append(
                GcModuleSlotEvidence(
                    slot.key,
                    slot.family,
                    slot.role,
                    expected[slot.key],
                    None,
                    "unknown",
                    False,
                    0.0,
                    0.0,
                    0.0,
                )
            )
            continue
        actual = match.name if match.status == "matched" else None
        slots.append(
            GcModuleSlotEvidence(
                slot.key,
                slot.family,
                slot.role,
                expected[slot.key],
                actual,
                match.status,
                match.status == "matched" and actual == expected[slot.key],
                match.confidence,
                match.margin,
                match.green_fraction,
            )
        )
    return GcModuleLoadoutEvidence(tuple(slots))


def ensure_gc_module_loadout(
    requirements: Mapping[str, Any],
    *,
    screenshot=None,
    capture_fn: Callable[[], Any] = capture_adb_screenshot,
    detector: Callable[[Any], Mapping[str, Any]] = detect_state_and_overlays,
    safe_tap_fn: Callable[..., bool] = safe_tap,
    swipe_fn: Callable[[str], bool] = swipe_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    evaluate_fn: Callable[..., GcModuleLoadoutEvidence] = evaluate_gc_module_loadout,
    equip_fn: Optional[Callable[[GcModuleSlotEvidence], Any]] = None,
    temporary_equip_fn: Optional[
        Callable[[GcModuleSlotEvidence, set[str]], Any]
    ] = None,
    repair_observer_fn: Optional[
        Callable[[tuple[GcModuleSlotEvidence, ...]], None]
    ] = None,
    catalog: Optional[ModuleIconCatalog] = None,
) -> GcModuleLoadoutEvidence:
    """Correct a GC module loadout while remaining on the Modules screen.

    Every occupied slot is replaced through the game's verified level-transfer
    prompt, so module levels behave as persistent slot levels. A Primary/Assist
    cycle is broken with a verified level-1 same-family temporary module rather
    than Unequip. Every inventory choice is authoritatively classified by icon
    before its detail name is confirmed, and the complete settled overview is
    re-evaluated after each transition. Live repair selects the Ancestral
    inventory filter once before its first replacement and restores all
    rarities only after the complete repaired loadout is verified.
    """

    selected_catalog = catalog or load_module_icon_catalog()
    expected = normalize_gc_module_requirements(
        requirements,
        catalog=selected_catalog,
    )
    current = screenshot if screenshot is not None else capture_fn()
    _require_modules(current, detector)
    ancestral_filter_active = False

    def ensure_ancestral_filter() -> None:
        nonlocal ancestral_filter_active
        if ancestral_filter_active:
            return
        _set_module_rarity_filter(
            "ancestral",
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            sleep_fn=sleep_fn,
        )
        ancestral_filter_active = True

    def live_equip(slot: GcModuleSlotEvidence):
        ensure_ancestral_filter()
        return _equip_inventory_module(
            slot,
            capture_fn=capture_fn,
            detector=detector,
            safe_tap_fn=safe_tap_fn,
            swipe_fn=swipe_fn,
            sleep_fn=sleep_fn,
            catalog=selected_catalog,
            require_level_transfer=True,
        )

    def live_temporary_equip(
        slot: GcModuleSlotEvidence,
        excluded_names: set[str],
    ):
        ensure_ancestral_filter()
        attempted: list[str] = []
        for module in selected_catalog.modules:
            if module.family != slot.family or module.name in excluded_names:
                continue
            attempted.append(module.name)
            temporary_slot = replace(slot, expected=module.name)
            try:
                result = _equip_inventory_module(
                    temporary_slot,
                    capture_fn=capture_fn,
                    detector=detector,
                    safe_tap_fn=safe_tap_fn,
                    swipe_fn=swipe_fn,
                    sleep_fn=sleep_fn,
                    catalog=selected_catalog,
                    required_inventory_level=1,
                    require_level_transfer=True,
                )
            except ModuleInventorySelectionError as exc:
                log(
                    "[MODULE_LOADOUT] Cannot use "
                    f"{module.name} as the {slot.family} level-transfer "
                    f"intermediate: {exc}",
                    "WARN",
                )
                continue
            log(
                "[MODULE_LOADOUT] Preserved "
                f"{slot.slot_key} through level-1 {module.name}",
                "INFO",
            )
            return result
        raise ModuleLoadoutCorrectionError(
            f"no verified level-1 {slot.family} temporary module was available "
            f"outside the desired loadout (tried={attempted})"
        )

    equip_action = equip_fn or live_equip
    temporary_action = temporary_equip_fn or live_temporary_equip
    announced = False

    for _step in range(_MAX_CORRECTION_STEPS):
        evidence = evaluate_fn(current, expected, catalog=selected_catalog)
        if evidence.valid:
            if ancestral_filter_active:
                current = _set_module_rarity_filter(
                    "all",
                    capture_fn=capture_fn,
                    detector=detector,
                    safe_tap_fn=safe_tap_fn,
                    sleep_fn=sleep_fn,
                )
                evidence = evaluate_fn(
                    current,
                    expected,
                    catalog=selected_catalog,
                )
            return evidence

        uncertain = [
            slot
            for slot in evidence.slots
            if not slot.valid and slot.match_status != "matched"
        ]
        if uncertain:
            labels = ", ".join(
                f"{slot.slot_key}={slot.match_status}" for slot in uncertain
            )
            raise ModuleLoadoutCorrectionError(
                "refusing module correction with uncertain overview evidence: "
                + labels
            )

        invalid = [slot for slot in evidence.slots if not slot.valid]
        if not announced:
            if repair_observer_fn is not None:
                repair_observer_fn(tuple(invalid))
            log(
                "[MODULE_LOADOUT] Repairing authoritative loadout mismatches: "
                + ", ".join(
                    f"{slot.slot_key}={slot.actual}->{slot.expected}"
                    for slot in invalid
                ),
                "INFO",
                console=True,
            )
            announced = True

        equipped_names = {
            slot.actual for slot in evidence.slots if slot.actual is not None
        }
        direct = next(
            (slot for slot in invalid if slot.expected not in equipped_names),
            None,
        )
        if direct is not None:
            current = equip_action(direct)
            _require_modules(current, detector)
            continue

        cycle = next((slot for slot in invalid if slot.actual is not None), None)
        if cycle is None:
            raise ModuleLoadoutCorrectionError(
                "module correction made no progress and no cycle could be broken"
            )
        excluded_names = set(expected.values()) | equipped_names
        log(
            "[MODULE_LOADOUT] "
            f"{cycle.family} Primary/Assist reassignment requires a level-1 "
            f"intermediate before moving {cycle.actual}",
            "DEBUG",
        )
        current = temporary_action(cycle, excluded_names)
        _require_modules(current, detector)

    raise ModuleLoadoutCorrectionError(
        "module correction exceeded its bounded transition count"
    )


def _require_modules(frame, detector) -> None:
    if frame is None:
        raise ModuleLoadoutCorrectionError("module capture failed")
    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape[0] < 1920
        or frame.shape[1] < 1080
        or float(np.mean(np.max(frame[:, :, :3], axis=2) < 8)) >= 0.5
    ):
        raise ModuleLoadoutCorrectionError("module capture was incomplete")
    detection = detector(frame)
    if detection.get("state") != "MODULES":
        raise ModuleLoadoutCorrectionError(
            f"expected MODULES, got {detection.get('state')!r}"
        )


def _capture_modules(capture_fn, detector):
    frame = capture_fn()
    _require_modules(frame, detector)
    return frame


def _wait_for(
    predicate,
    *,
    capture_fn,
    detector,
    sleep_fn,
    timeout: float = 8.0,
    reason: str,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "MODULES":
            if predicate(frame):
                return frame
        sleep_fn(0.25)
    raise ModuleLoadoutCorrectionError(f"timed out waiting for {reason}")


def _crop_centered(frame, center: tuple[int, int], size: int):
    half = size // 2
    x, y = center
    return frame[y - half : y + half, x - half : x + half]


def _normalized(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(text).upper()).strip()


def _read_detail(frame) -> ModuleDetailEvidence:
    rarity, _ = ocr_text_and_conf(frame[185:285, 400:760], psm=6)
    name, _ = ocr_text_and_conf(frame[285:365, 405:970], psm=7)
    equipped, _ = ocr_text_and_conf(frame[440:510, 400:850], psm=7)
    action, _ = ocr_text_and_conf(frame[1620:1740, 130:405], psm=7)
    level_text, _ = ocr_text_and_conf(frame[480:560, 150:400], psm=6)
    level_match = re.search(r"\bL[VY]\s*\.?\s*(\d+)", level_text.upper())
    return ModuleDetailEvidence(
        name=name.strip(),
        rarity=_normalized(rarity),
        equipped=_normalized(equipped),
        action=_normalized(action),
        level=int(level_match.group(1)) if level_match else None,
    )


def _detail_for(frame, expected: str, *, action: str) -> bool:
    detail = _read_detail(frame)
    return (
        "ANCESTRAL" in detail.rarity
        and _normalized(detail.name) == _normalized(expected)
        and action in detail.action.split()
    )


def _detail_ready(frame) -> bool:
    detail = _read_detail(frame)
    return (
        "ANCESTRAL" in detail.rarity
        and bool(_normalized(detail.name))
        and bool({"EQUIP", "UNEQUIP"} & set(detail.action.split()))
    )


def _filter_panel_visible(frame) -> bool:
    text, _ = ocr_text_and_conf(frame[420:1630, 430:910], psm=6)
    normalized = _normalized(text)
    return all(token in normalized for token in ("NONE", "COMMON", "ANCESTRAL"))


def _filter_option_visible(frame, option: str) -> bool:
    regions = {
        "NONE": (430, 420, 800, 550),
        # Keep each single-line OCR crop clear of the adjacent rarity row and
        # the checkbox. In particular, the former Ancestral crop also
        # included Mythic+, causing otherwise-clear live panels to fail closed.
        "ANCESTRAL": (430, 1420, 800, 1535),
        "ALL RARITIES": (430, 1525, 800, 1630),
    }
    x1, y1, x2, y2 = regions[option]
    text, _ = ocr_text_and_conf(frame[y1:y2, x1:x2], psm=7)
    normalized = _normalized(text)
    return all(token in normalized for token in option.split())


def _filter_label(frame) -> str:
    text, _ = ocr_text_and_conf(frame[1630:1750, 450:880], psm=7)
    return _normalized(text)


def _set_module_rarity_filter(
    mode: str,
    *,
    capture_fn,
    detector,
    safe_tap_fn,
    sleep_fn,
):
    frame = _capture_modules(capture_fn, detector)
    if not _filter_panel_visible(frame):
        if not safe_tap_fn(
            "buttons.module:rarity_filter",
            dispatch="now",
            verification=TapVerification(
                screenshot=frame,
                target_region=(450, 1630, 430, 120),
                description="module_rarity_filter:closed",
                verifier=lambda candidate: (
                    detector(candidate).get("state") == "MODULES"
                    and not _filter_panel_visible(candidate)
                    and bool(_filter_label(candidate))
                ),
            ),
        ):
            raise ModuleLoadoutCorrectionError("failed to open module rarity filter")
    frame = _wait_for(
        lambda candidate: (
            _filter_panel_visible(candidate)
            and _filter_option_visible(candidate, "NONE")
        ),
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
        reason="settled module rarity filter",
    )

    if mode == "ancestral":
        if not safe_tap_fn(
            "buttons.module:rarity_none",
            dispatch="now",
            verification=TapVerification(
                screenshot=frame,
                target_region=(430, 420, 480, 120),
                description="module_rarity_filter:none",
                verifier=lambda candidate: (
                    _filter_panel_visible(candidate)
                    and _filter_option_visible(candidate, "NONE")
                ),
            ),
        ):
            raise ModuleLoadoutCorrectionError("failed to clear module rarities")
        frame = _wait_for(
            lambda candidate: (
                _filter_panel_visible(candidate)
                and _filter_option_visible(candidate, "ANCESTRAL")
            ),
            capture_fn=capture_fn,
            detector=detector,
            sleep_fn=sleep_fn,
            reason="settled cleared module rarity filter",
        )
        if not safe_tap_fn(
            "buttons.module:rarity_ancestral",
            dispatch="now",
            verification=TapVerification(
                screenshot=frame,
                target_region=(760, 1400, 250, 150),
                description="module_rarity_filter:ancestral",
                verifier=lambda candidate: (
                    _filter_panel_visible(candidate)
                    and _filter_option_visible(candidate, "ANCESTRAL")
                ),
            ),
        ):
            raise ModuleLoadoutCorrectionError("failed to select Ancestral rarity")
        wanted = "ANCESTRAL"
    elif mode == "all":
        if not safe_tap_fn(
            "buttons.module:rarity_all",
            dispatch="now",
            verification=TapVerification(
                screenshot=frame,
                target_region=(450, 1510, 270, 130),
                description="module_rarity_filter:all",
                verifier=lambda candidate: (
                    _filter_panel_visible(candidate)
                    and _filter_option_visible(candidate, "ALL RARITIES")
                ),
            ),
        ):
            raise ModuleLoadoutCorrectionError("failed to select all module rarities")
        wanted = "ALL RARITIES"
    else:
        raise ValueError(f"unsupported module rarity filter mode: {mode!r}")

    frame = _wait_for(
        lambda candidate: (
            _filter_panel_visible(candidate)
            and _filter_option_visible(
                candidate,
                "ANCESTRAL" if mode == "ancestral" else "ALL RARITIES",
            )
        ),
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
        reason=f"settled {wanted} module rarity selection",
    )
    if not safe_tap_fn(
        "buttons.module:rarity_filter",
        dispatch="now",
        verification=TapVerification(
            screenshot=frame,
            target_region=(450, 1630, 430, 120),
            description=f"module_rarity_filter:close:{wanted}",
            verifier=lambda candidate: (
                _filter_panel_visible(candidate)
                and bool(_filter_label(candidate))
            ),
        ),
    ):
        raise ModuleLoadoutCorrectionError("failed to close module rarity filter")
    return _wait_for(
        lambda candidate: wanted in _filter_label(candidate),
        capture_fn=capture_fn,
        detector=detector,
        sleep_fn=sleep_fn,
        reason=f"{wanted} module filter",
    )


def _inventory_fingerprint(frame) -> np.ndarray:
    region = frame[980:1630, 0:1080]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (54, 32), interpolation=cv2.INTER_AREA)


def _inventory_candidates(frame, target: str, catalog: ModuleIconCatalog):
    candidates: list[tuple[float, float, tuple[int, int]]] = []
    for y in INVENTORY_ROWS:
        for x in INVENTORY_COLUMNS:
            center = (x, y)
            frame_crop = _crop_centered(
                frame,
                center,
                INVENTORY_FRAME_CROP_SIZE,
            )
            if (
                ancestral_green_fraction(frame_crop, catalog=catalog)
                < catalog.minimum_green_fraction
            ):
                continue
            icon_crops = (
                _crop_centered(
                    frame,
                    (x + x_offset, y + y_offset),
                    INVENTORY_ICON_CROP_SIZE,
                )
                for y_offset in range(
                    -catalog.alignment_radius,
                    catalog.alignment_radius + 1,
                )
                for x_offset in range(
                    -catalog.alignment_radius,
                    catalog.alignment_radius + 1,
                )
            )
            ranked = rank_module_icon_candidates(
                icon_crops,
                catalog=catalog,
            )
            if len(ranked) < 2:
                continue
            scores = [
                (
                    score,
                    module.name,
                )
                for score, module in ranked
            ]
            best_score, best_name = scores[0]
            runner_score, _runner_name = scores[1]
            margin = best_score - runner_score
            if best_name != target:
                continue
            if best_score < catalog.inventory_minimum_confidence:
                continue
            if margin < catalog.inventory_minimum_margin:
                continue
            candidates.append((best_score, margin, center))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _scroll_inventory_to_top(
    current,
    *,
    capture_fn,
    detector,
    swipe_fn,
    sleep_fn,
):
    for _ in range(_MAX_INVENTORY_SCROLLS):
        before = _inventory_fingerprint(current)
        if not swipe_fn("gesture_targets.goto_previous:module_inventory"):
            raise ModuleLoadoutCorrectionError("module inventory top swipe failed")
        sleep_fn(0.6)
        updated = _capture_modules(capture_fn, detector)
        after = _inventory_fingerprint(updated)
        current = updated
        if float(cv2.absdiff(before, after).mean()) < 0.5:
            return current
    raise ModuleLoadoutCorrectionError(
        "module inventory did not reach its top within the bounded swipe count"
    )


def _find_inventory_detail(
    target: str,
    *,
    capture_fn,
    detector,
    safe_tap_fn,
    swipe_fn,
    sleep_fn,
    catalog,
    required_level: Optional[int] = None,
):
    current = _capture_modules(capture_fn, detector)
    current = _scroll_inventory_to_top(
        current,
        capture_fn=capture_fn,
        detector=detector,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
    )
    seen_names: set[str] = set()
    seen_viewports: list[np.ndarray] = []

    for scroll_index in range(_MAX_INVENTORY_SCROLLS + 1):
        fingerprint = _inventory_fingerprint(current)
        if any(float(cv2.absdiff(fingerprint, seen).mean()) < 0.5 for seen in seen_viewports):
            break
        seen_viewports.append(fingerprint)

        for score, margin, center in _inventory_candidates(
            current,
            target,
            catalog,
        ):
            fresh = _capture_modules(capture_fn, detector)
            frame_crop = _crop_centered(
                fresh,
                center,
                INVENTORY_FRAME_CROP_SIZE,
            )
            if (
                ancestral_green_fraction(frame_crop, catalog=catalog)
                < catalog.minimum_green_fraction
            ):
                continue
            if not safe_tap_fn(
                center,
                dispatch="now",
                log_label=(
                    f"gc_module_inventory_candidate:{target}:"
                    f"score={score:.3f}:margin={margin:.3f}"
                ),
                verification=TapVerification(
                    screenshot=fresh,
                    target_region=(
                        center[0] - INVENTORY_FRAME_CROP_SIZE // 2,
                        center[1] - INVENTORY_FRAME_CROP_SIZE // 2,
                        INVENTORY_FRAME_CROP_SIZE,
                        INVENTORY_FRAME_CROP_SIZE,
                    ),
                    description=f"ancestral_module_candidate:{target}",
                    verifier=lambda candidate, point=center: (
                        detector(candidate).get("state") == "MODULES"
                        and ancestral_green_fraction(
                            _crop_centered(
                                candidate,
                                point,
                                INVENTORY_FRAME_CROP_SIZE,
                            ),
                            catalog=catalog,
                        )
                        >= catalog.minimum_green_fraction
                    ),
                ),
            ):
                raise ModuleLoadoutCorrectionError(
                    f"failed to open inventory candidate for {target}"
                )
            sleep_fn(MODULE_DETAIL_SETTLE_SECONDS)
            detail = _wait_for(
                _detail_ready,
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
                reason="complete Ancestral module detail",
            )
            observed = _read_detail(detail)
            normalized_name = _normalized(observed.name)
            if (
                normalized_name == _normalized(target)
                and "EQUIP" in observed.action.split()
                and (
                    required_level is None
                    or observed.level == required_level
                )
            ):
                return detail
            if normalized_name:
                seen_names.add(normalized_name)
            if not safe_tap_fn(
                "buttons.close:module_detail",
                dispatch="now",
                verification=TapVerification(
                    screenshot=detail,
                    target_region=(860, 160, 140, 130),
                    description=f"module_detail:{observed.name}",
                    verifier=_detail_ready,
                ),
            ):
                raise ModuleLoadoutCorrectionError("failed to close module detail")
            current = _wait_for(
                lambda frame: "ANCESTRAL" not in _read_detail(frame).rarity,
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
                reason="Modules inventory overview",
            )

        if scroll_index >= _MAX_INVENTORY_SCROLLS:
            break
        before = _inventory_fingerprint(current)
        if not swipe_fn("gesture_targets.goto_next:module_inventory"):
            raise ModuleLoadoutCorrectionError("module inventory swipe failed")
        sleep_fn(0.6)
        updated = _capture_modules(capture_fn, detector)
        after = _inventory_fingerprint(updated)
        if float(cv2.absdiff(before, after).mean()) < 0.5:
            break
        current = updated

    suffix = f" after reviewing {len(seen_names)} named candidate(s)"
    raise ModuleInventorySelectionError(
        f"Ancestral inventory module {target!r} was not found{suffix}"
    )


def _role_prompt_visible(frame) -> bool:
    crop = frame[850:1250, 220:860]
    for psm in (6, 11):
        text, _ = ocr_text_and_conf(crop, psm=psm)
        normalized = _normalized(text)
        if "PRIMARY" in normalized and "ASSIST" in normalized:
            return True
    return False


def _transfer_prompt_visible(frame) -> bool:
    text, _ = ocr_text_and_conf(frame[780:1280, 160:920], psm=6)
    normalized = _normalized(text)
    return "TRANSFER" in normalized and "LEVEL" in normalized


def _overview_visible(frame) -> bool:
    detail = _read_detail(frame)
    return (
        "ANCESTRAL" not in detail.rarity
        and not _role_prompt_visible(frame)
        and not _transfer_prompt_visible(frame)
    )


def _settled_ancestral_overview_visible(
    frame,
    *,
    catalog: ModuleIconCatalog,
) -> bool:
    return _overview_visible(frame) and all(
        match.status == "matched"
        for match in identify_equipped_ancestral_modules(
            frame,
            catalog=catalog,
        )
    )


def _equip_inventory_module(
    slot: GcModuleSlotEvidence,
    *,
    capture_fn,
    detector,
    safe_tap_fn,
    swipe_fn,
    sleep_fn,
    catalog,
    required_inventory_level: Optional[int] = None,
    require_level_transfer: bool = True,
    allow_level_transfer: bool = True,
):
    detail = _find_inventory_detail(
        slot.expected,
        capture_fn=capture_fn,
        detector=detector,
        safe_tap_fn=safe_tap_fn,
        swipe_fn=swipe_fn,
        sleep_fn=sleep_fn,
        catalog=catalog,
        required_level=required_inventory_level,
    )
    if not _detail_for(detail, slot.expected, action="EQUIP"):
        raise ModuleLoadoutCorrectionError(
            f"inventory detail guard failed for {slot.expected}"
        )
    role_prompt = None
    for attempt in range(1, 3):
        if not safe_tap_fn(
            "buttons.module:detail_equip_toggle",
            dispatch="now",
            verification=TapVerification(
                screenshot=detail,
                target_region=(120, 1610, 310, 130),
                description=f"module_detail:equip:{slot.expected}",
                verifier=lambda frame: _detail_for(
                    frame,
                    slot.expected,
                    action="EQUIP",
                ),
            ),
        ):
            raise ModuleLoadoutCorrectionError(
                f"Equip tap failed for {slot.expected}"
            )
        try:
            role_prompt = _wait_for(
                _role_prompt_visible,
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
                timeout=4.0,
                reason="Primary/Assist module role prompt",
            )
            break
        except ModuleLoadoutCorrectionError:
            if attempt >= 2:
                raise
            detail = _capture_modules(capture_fn, detector)
            if _role_prompt_visible(detail):
                role_prompt = detail
                break
            if not _detail_for(detail, slot.expected, action="EQUIP"):
                raise ModuleLoadoutCorrectionError(
                    f"Equip transition left the verified {slot.expected} detail "
                    "without opening the role prompt"
                )
            log(
                f"[MODULE_LOADOUT] Equip input for {slot.expected} did not "
                "open the role prompt; retrying once",
                "WARN",
            )

    if role_prompt is None:
        raise ModuleLoadoutCorrectionError(
            f"role prompt was unavailable for {slot.expected}"
        )
    role_key = f"buttons.module:select_{slot.role}"
    frame = role_prompt
    if not _role_prompt_visible(frame):
        raise ModuleLoadoutCorrectionError("module role prompt guard was lost")
    role_region = (
        (300, 1010, 210, 170)
        if slot.role == "primary"
        else (570, 1010, 220, 170)
    )
    if not safe_tap_fn(
        role_key,
        dispatch="now",
        verification=TapVerification(
            screenshot=frame,
            target_region=role_region,
            description=f"module_role:{slot.role}",
            verifier=_role_prompt_visible,
        ),
    ):
        raise ModuleLoadoutCorrectionError(
            f"failed to select {slot.role} for {slot.expected}"
        )

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        frame = _capture_modules(capture_fn, detector)
        if _transfer_prompt_visible(frame):
            if not allow_level_transfer:
                raise ModuleLoadoutCorrectionError(
                    f"{slot.expected} unexpectedly offered level transfer for "
                    f"empty {slot.slot_key}"
                )
            if not safe_tap_fn(
                "buttons.module:accept_level_transfer",
                dispatch="now",
                verification=TapVerification(
                    screenshot=frame,
                    target_region=(590, 1040, 270, 170),
                    description="module_level_transfer:accept",
                    verifier=_transfer_prompt_visible,
                ),
            ):
                raise ModuleLoadoutCorrectionError(
                    "failed to accept module level transfer"
                )
            return _wait_for(
                lambda candidate: _settled_ancestral_overview_visible(
                    candidate,
                    catalog=catalog,
                ),
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
                reason=(
                    "settled Ancestral Modules overview after accepted "
                    "level transfer"
                ),
            )
        if _overview_visible(frame):
            if require_level_transfer:
                raise ModuleLoadoutCorrectionError(
                    f"{slot.expected} replaced occupied {slot.slot_key} "
                    "without offering the required level transfer"
                )
            return _wait_for(
                lambda candidate: _settled_ancestral_overview_visible(
                    candidate,
                    catalog=catalog,
                ),
                capture_fn=capture_fn,
                detector=detector,
                sleep_fn=sleep_fn,
                reason="settled Ancestral Modules overview",
            )
        sleep_fn(0.25)
    raise ModuleLoadoutCorrectionError(
        f"timed out equipping {slot.expected} as {slot.role}"
    )


__all__ = [
    "gc_module_loadout_evidence_from_dict",
    "GcModuleLoadoutEvidence",
    "GcModuleSlotEvidence",
    "ModuleLoadoutCorrectionError",
    "ensure_gc_module_loadout",
    "evaluate_gc_module_loadout",
    "normalize_gc_module_requirements",
]
