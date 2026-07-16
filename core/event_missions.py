"""Guarded Event Mission inventory capture and row OCR."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
EVENT_CONTENT_REGION = (0, 840, 1080, 900)
_EVENT_TITLE_REGION = (20, 85, 760, 95)
_EVENT_REMAINING_REGION = (650, 175, 420, 80)


@dataclass(frozen=True)
class EventMissionObservation:
    """One named Event Mission row observed in a complete list inventory."""

    name: str
    incomplete: bool
    progress_current: Optional[int] = None
    progress_target: Optional[int] = None
    progress_text: Optional[str] = None
    confidence: float = -1.0

    @property
    def progress(self) -> Optional[str]:
        if self.progress_text:
            return self.progress_text
        if self.progress_current is None or self.progress_target is None:
            return None
        return f"{self.progress_current}/{self.progress_target}"


@dataclass(frozen=True)
class EventMissionInventory:
    """A bounded top-to-bottom Event Mission observation."""

    event_name: str
    remaining_seconds: Optional[int]
    missions: Tuple[EventMissionObservation, ...]
    complete: bool
    source_reason: str


def capture_event_mission_inventory(screenshot: Frame) -> EventMissionInventory:
    """Capture and OCR the full Event Mission list with guarded swipes."""

    top = scroll_to_edge(
        "gesture_targets.goto_top:event_missions",
        source_label="indicators.event",
        screenshot=screenshot,
        progress_region=EVENT_CONTENT_REGION,
        max_swipes=8,
        settle_s=0.8,
        stable_threshold=2.0,
    )
    if not top.success or top.screenshot is None:
        frames = (top.screenshot,) if top.screenshot is not None else ()
        return ocr_event_mission_inventory(
            frames,
            complete=False,
            source_reason=f"top_{top.reason}",
        )

    capture = capture_scroll_to_edge(
        "gesture_targets.goto_next:event_missions",
        source_label="indicators.event",
        screenshot=top.screenshot,
        progress_region=EVENT_CONTENT_REGION,
        max_swipes=16,
        settle_s=0.8,
        stable_threshold=2.0,
    )
    return ocr_event_mission_inventory(
        capture.screenshots,
        complete=capture.success and capture.reason == "edge_reached",
        source_reason=capture.reason,
    )


def ocr_event_mission_inventory(
    frames: Sequence[Frame],
    *,
    complete: bool,
    source_reason: str,
) -> EventMissionInventory:
    """OCR named mission rows from overlapping Event viewports."""

    usable = tuple(frame for frame in frames if frame is not None and frame.size)
    if not usable:
        return EventMissionInventory("", None, (), False, source_reason)

    event_name = _ocr_event_name(usable[0])
    remaining_seconds = _ocr_remaining_seconds(usable[0])
    observations: dict[str, EventMissionObservation] = {}
    for frame in usable:
        for top, bottom in _find_row_bounds(frame):
            observation = _ocr_row(frame[top:bottom])
            if observation is None:
                continue
            key = _mission_key(observation.name)
            previous = observations.get(key)
            if previous is None or _observation_quality(
                observation
            ) > _observation_quality(previous):
                observations[key] = observation

    missions = tuple(
        sorted(observations.values(), key=lambda item: item.name.casefold())
    )
    # A successful scroll with no readable mission rows is not authoritative:
    # Event lists always contain rows, even when every mission is complete.
    authoritative = bool(complete and event_name and missions)
    return EventMissionInventory(
        event_name=event_name,
        remaining_seconds=remaining_seconds,
        missions=missions,
        complete=authoritative,
        source_reason=(
            source_reason if authoritative else f"{source_reason}:ocr_incomplete"
        ),
    )


def _find_row_bounds(frame: Frame) -> Tuple[Tuple[int, int], ...]:
    """Locate complete cyan-bordered mission cards in one viewport."""

    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    cyan = cv2.inRange(
        hsv,
        np.array([75, 40, 120], dtype=np.uint8),
        np.array([110, 255, 255], dtype=np.uint8),
    )
    contours, _ = cv2.findContours(cyan, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    line_ys = []
    direct_rows = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if x > 40 or w < int(width * 0.92):
            continue
        if h <= 8:
            line_ys.append(y)
        elif 180 <= h <= 320:
            direct_rows.append((y, y + h))

    grouped_ys = []
    for y in sorted(line_ys):
        if not grouped_ys or y - grouped_ys[-1][-1] > 12:
            grouped_ys.append([y])
        else:
            grouped_ys[-1].append(y)
    boundaries = [min(group) for group in grouped_ys]
    for first, second in zip(boundaries, boundaries[1:]):
        if 180 <= second - first <= 320:
            direct_rows.append((first, second))

    rows = {
        (max(0, top), min(height, bottom))
        for top, bottom in direct_rows
        if 0 <= top < bottom <= height and bottom - top >= 180
    }
    return tuple(sorted(rows))


def _ocr_row(row: Frame) -> Optional[EventMissionObservation]:
    height, width = row.shape[:2]
    if height < 180 or width < 900:
        return None

    description_crop = row[15 : min(height, 125), 25 : min(width, 900)]
    name, confidence = ocr_text_and_conf(description_crop, psm=6)
    name = _normalize_description(name)
    if confidence < 60.0 or len(re.findall(r"[A-Za-z]", name)) < 4:
        return None
    if name.upper().startswith("EVENT BOOST"):
        return None

    status_text, _ = ocr_text_and_conf(row[20 : height - 10, 25 : width - 25], psm=6)
    status = re.sub(r"[^A-Z]", "", status_text.upper())
    incomplete = "CLAIM" not in status and "COMPLET" not in status

    current = target = None
    normalized_progress = None
    if incomplete:
        progress_crop = row[
            int(height * 0.48) : int(height * 0.90),
            int(width * 0.34) : int(width * 0.68),
        ]
        if progress_crop.size:
            gray = cv2.cvtColor(progress_crop, cv2.COLOR_BGR2GRAY)
            _, white = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
            enlarged = cv2.resize(white, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            progress_text, _ = ocr_text_and_conf(
                enlarged,
                psm=7,
                config_extra=(
                    "-c tessedit_char_whitelist="
                    "0123456789/.,KMBTQkmbtq"
                ),
            )
            match = re.search(
                r"([0-9][0-9.,]*[KMBTQ]?)\s*/\s*"
                r"([0-9][0-9.,]*[KMBTQ]?)",
                progress_text.upper(),
            )
            if match:
                first, second = match.groups()
                normalized_progress = f"{first}/{second}"
                if first.isdigit() and second.isdigit():
                    current, target = int(first), int(second)

    return EventMissionObservation(
        name=name,
        incomplete=incomplete,
        progress_current=current,
        progress_target=target,
        progress_text=normalized_progress,
        confidence=confidence,
    )


def _ocr_event_name(frame: Frame) -> str:
    crop = _crop(frame, _EVENT_TITLE_REGION)
    text, confidence = ocr_text_and_conf(crop, psm=7)
    if confidence < 60.0:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip(" -")
    normalized = re.sub(r"^EVENT\s*[-:]\s*", "", normalized, flags=re.IGNORECASE)
    return normalized.strip()


def _ocr_remaining_seconds(frame: Frame) -> Optional[int]:
    crop = _crop(frame, _EVENT_REMAINING_REGION)
    text, _ = ocr_text_and_conf(crop, psm=7)
    days = re.search(r"(\d+)\s*d", text, flags=re.IGNORECASE)
    hours = re.search(r"(\d+)\s*h", text, flags=re.IGNORECASE)
    if not days and not hours:
        return None
    remaining_hours = (
        (int(days.group(1)) if days else 0) * 24
        + (int(hours.group(1)) if hours else 0)
    )
    return remaining_hours * 3600


def _crop(frame: Frame, region: Tuple[int, int, int, int]) -> Frame:
    x, y, w, h = region
    return frame[y : y + h, x : x + w]


def _normalize_description(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .,:;|_-")


def _mission_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def _observation_quality(observation: EventMissionObservation) -> tuple[int, float]:
    return (1 if observation.progress is not None else 0, observation.confidence)


__all__ = [
    "EventMissionInventory",
    "EventMissionObservation",
    "capture_event_mission_inventory",
    "ocr_event_mission_inventory",
]
