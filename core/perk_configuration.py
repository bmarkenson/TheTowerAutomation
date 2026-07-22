"""Structured OCR for read-only Home Perks configuration captures."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from core.battle_perks import ocr_perk_rows


Frame = np.ndarray
RowTextFn = Callable[[Frame], tuple[str, float]]

# In the configuration panel selected tiles use the same hue as available
# tiles but a deliberately darker fill.  The retained First Perk fixture reads
# median V=83 for selected and V=137 for available rows.
MAX_SELECTED_BACKGROUND_VALUE = 110.0
DEFAULT_CONFIDENCE_THRESHOLD = 70.0

ORDER_SEMANTICS = {
    "perk_first_choice": "single_choice",
    "perk_bans": "display_order",
    "perk_auto_pick_order": "top_to_bottom_priority",
}


def parse_perk_configuration_selection(
    frames: Sequence[Frame],
    *,
    field: str,
    source_complete: bool,
    source_reason: str,
    evidence_images: Sequence[str] = (),
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    text_fn: Optional[RowTextFn] = None,
) -> dict[str, Any]:
    """Extract the dark selected rows from overlapping configuration pages."""

    if field not in ORDER_SEMANTICS:
        raise ValueError(f"unknown Perks configuration field {field!r}")
    selected: list[dict[str, Any]] = []
    raw_pages = []
    for page, frame in enumerate(frames, start=1):
        rows = ocr_perk_rows(frame, text_fn=text_fn)
        raw_pages.append(
            {
                "page": page,
                "rows": rows,
            }
        )
        for row in rows:
            if float(row["background_value_median"]) > MAX_SELECTED_BACKGROUND_VALUE:
                continue
            existing = _find_duplicate(selected, str(row["display_text"]))
            observation = {
                "page": page,
                "top": row["top"],
                "confidence": row["confidence"],
            }
            if existing is not None:
                existing["observations"].append(observation)
                if float(row["confidence"]) > float(existing["confidence"]):
                    existing.update(
                        text_raw=row["text_raw"],
                        display_text=row["display_text"],
                        key=row["key"],
                        confidence=row["confidence"],
                    )
                continue
            selected.append(
                {
                    "rank": len(selected) + 1,
                    "display_text": row["display_text"],
                    "text_raw": row["text_raw"],
                    "key": row["key"],
                    "confidence": row["confidence"],
                    "observations": [observation],
                }
            )

    low_confidence = [
        item["key"]
        for item in selected
        if float(item["confidence"]) < float(confidence_threshold)
    ]
    warnings = []
    if not source_complete:
        warnings.append(f"Perks configuration capture was incomplete: {source_reason}")
    if low_confidence:
        warnings.append("Low-confidence configured perks: " + ", ".join(low_confidence))
    if field == "perk_first_choice" and len(selected) != 1:
        warnings.append(
            f"First Perk capture contained {len(selected)} selected rows instead of one"
        )
    if field == "perk_auto_pick_order" and not selected:
        warnings.append("Auto Pick capture contained no selected priority rows")
    valid = bool(source_complete and not low_confidence and not warnings)
    return {
        "source_method": "scrolling_screenshot_ocr",
        "page_count": len(frames),
        "order_semantics": ORDER_SEMANTICS[field],
        "selected": selected,
        "evidence_images": list(evidence_images),
        "raw_pages": raw_pages,
        "quality": {
            "valid": valid,
            "source_complete": bool(source_complete),
            "source_reason": source_reason,
            "confidence_threshold": float(confidence_threshold),
            "selected_count": len(selected),
            "low_confidence": low_confidence,
            "warnings": warnings,
        },
    }


def _find_duplicate(
    selected: Sequence[Mapping[str, Any]],
    display_text: str,
) -> Optional[dict[str, Any]]:
    normalized = _comparison_text(display_text)
    for item in selected:
        existing = _comparison_text(str(item.get("display_text") or ""))
        if SequenceMatcher(None, existing, normalized).ratio() >= 0.88:
            return item  # type: ignore[return-value]
    return None


def _comparison_text(text: str) -> str:
    return re.sub(r"[^a-z0-9.%+-]+", " ", str(text or "").casefold()).strip()


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "MAX_SELECTED_BACKGROUND_VALUE",
    "ORDER_SEMANTICS",
    "parse_perk_configuration_selection",
]
