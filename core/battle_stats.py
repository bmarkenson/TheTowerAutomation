"""Structured capture and persistence for completed-battle statistics."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.battle_classification import (
    analyze_battle_type,
    observed_tier_for_record,
)
from utils.ocr_utils import ocr_text_and_conf

try:
    import pytesseract
except Exception:  # pragma: no cover - exercised through the unavailable path
    pytesseract = None


Frame = np.ndarray
OcrDataFn = Callable[[Frame], Mapping[str, Sequence[Any]]]

SCHEMA_VERSION = 4
DEFAULT_RECORDS_DIR = Path("logs/battles")
MORE_STATS_CROP = (140, 330, 800, 1370)
GAME_STATS_CROP = (40, 400, 995, 1110)
VALUE_COLUMN_X = 700
SAFE_LINE_TOP = 370
SAFE_LINE_BOTTOM = 1600
DEFAULT_CONFIDENCE_THRESHOLD = 50.0

_REQUIRED_BATTLE_REPORT_ROWS = {
    "game_time",
    "real_time",
    "tier",
    "wave",
    "killed_by",
    "coins_earned",
    "coins_per_hour",
    "cells_earned",
    "cells_per_hour",
}
_REQUIRED_CURRENCY_ROWS = {
    "cells_earned",
    "gems",
    "ad_gems",
    "gem_blocks_tapped",
    "fetch_gems",
    "medals",
    "reroll_shards_earned",
    "reroll_shards_fetched",
    "cannon_shards",
    "armor_shards",
    "generator_shards",
    "core_shards",
    "common_modules",
    "rare_modules",
}
_REQUIRED_MORE_STATS_SECTIONS = {
    "battle_report",
    "records",
    "damage",
    "damage_taken",
    "bonus_health_gained",
    "health_regenerated",
    "damage_blocked",
    "utility",
    "counts",
    "enemies_hit_by",
    "killed_with_effect_active",
    "total_enemies",
    "coins",
    "cash",
    "currencies",
    "enemies_destroyed_by",
}
_REQUIRED_GAME_STATS_FIELDS = {
    "highest_wave",
    "base_coins_earned",
    "ad_coins_earned",
}


def make_battle_id(captured_at: Optional[datetime] = None) -> str:
    """Return a second-resolution, timezone-aware identifier for one battle."""

    when = captured_at or datetime.now().astimezone()
    return "Battle" + when.strftime("%Y%m%dT%H%M%S%z")


def parse_tower_number(raw: str) -> Optional[Decimal]:
    """Parse The Tower's case-sensitive compact-number notation."""

    text = (raw or "").strip().replace(",", "")
    match = re.fullmatch(
        r"[$]?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMBTqQsSOND]|[a-z]{2})?",
        text,
    )
    if not match:
        return None

    try:
        value = Decimal(match.group(1))
    except (InvalidOperation, TypeError, ValueError):
        return None

    suffix = match.group(2)
    if not suffix:
        return value
    named_exponents = {
        "K": 3,
        "M": 6,
        "B": 9,
        "T": 12,
        "q": 15,
        "Q": 18,
        "s": 21,
        "S": 24,
        "O": 27,
        "N": 30,
        "D": 33,
    }
    exponent = named_exponents.get(suffix)
    if exponent is None:
        first, second = suffix
        index = (ord(first) - ord("a")) * 26 + (ord(second) - ord("a"))
        if index < 0:
            return None
        exponent = 36 + (index * 3)
    return value * (Decimal(10) ** exponent)


def parse_duration_seconds(raw: str) -> Optional[int]:
    """Parse a compact ``1d 5h 43m 19s``-style duration."""

    text = (raw or "").strip().lower()
    match = re.fullmatch(
        r"\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*"
        r"(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*",
        text,
    )
    if not match or not any(part is not None for part in match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_tower_number(value: Decimal) -> str:
    """Format a Decimal with the same case-sensitive suffix scale as the game."""

    if value == 0:
        return "0"
    exponents: list[tuple[int, str]] = [
        (3, "K"),
        (6, "M"),
        (9, "B"),
        (12, "T"),
        (15, "q"),
        (18, "Q"),
        (21, "s"),
        (24, "S"),
        (27, "O"),
        (30, "N"),
        (33, "D"),
    ]
    for index in range(26 * 26):
        suffix = chr(ord("a") + index // 26) + chr(ord("a") + index % 26)
        exponents.append((36 + index * 3, suffix))

    absolute = value.copy_abs()
    chosen: Optional[tuple[int, str]] = None
    for exponent, suffix in exponents:
        if absolute >= Decimal(10) ** exponent:
            chosen = (exponent, suffix)
        else:
            break
    scaled = value if chosen is None else value / (Decimal(10) ** chosen[0])
    rendered = format(scaled.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
    return rendered + (chosen[1] if chosen else "")


def ocr_game_stats(
    frame: Frame,
    *,
    text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """OCR the compact Game Stats dialog, including fields absent from More Stats."""

    crop = _crop(frame, GAME_STATS_CROP)
    raw_text, confidence = text_fn(crop, psm=6)
    normalized = " ".join((raw_text or "").split())

    fields: dict[str, Any] = {}
    patterns: list[tuple[str, str, Callable[[str], Any]]] = [
        ("wave", r"\bWave\s+(\d{1,7})\b", int),
        ("tier", r"\bTier\s+(\d{1,3})\b", int),
        ("highest_wave", r"Highest\s+Wave\s*:\s*(\d{1,7})\b", int),
        (
            "death_defies",
            r"Death\s+defied\s+(\d+)\s+times?\b",
            int,
        ),
        (
            "killed_by",
            r"Killed\s+By\s+(.+?)(?=\s+Death\s+defied|\s+coins\s+earned|$)",
            str,
        ),
    ]
    for key, pattern, converter in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            fields[key] = {
                "raw": match.group(1).strip(),
                "value": converter(match.group(1).strip()),
                "confidence": round(float(confidence), 1),
            }

    coins_match = re.search(
        r"coins\s+earned\s+ad\s+coins\s+earned\s+total\s+coins\s+"
        r"([0-9.]+\s*[KMBTqQsSOND]|[0-9.]+\s*[a-z]{2}|[0-9.]+)\s*.*?\+\s*"
        r"([0-9.]+\s*[KMBTqQsSOND]|[0-9.]+\s*[a-z]{2}|[0-9.]+)\s*.*?=\s*"
        r"([0-9.]+\s*[KMBTqQsSOND]|[0-9.]+\s*[a-z]{2}|[0-9.]+)",
        normalized,
        re.IGNORECASE,
    )
    if coins_match:
        for key, raw in zip(
            ("base_coins_earned", "ad_coins_earned", "total_coins_earned"),
            coins_match.groups(),
        ):
            parsed = parse_tower_number(raw)
            fields[key] = {
                "raw": raw.replace(" ", ""),
                "decimal": str(parsed) if parsed is not None else None,
                "confidence": round(float(confidence), 1),
            }

    return {
        "raw_text": normalized,
        "confidence": round(float(confidence), 1),
        "fields": fields,
    }


def ocr_more_stats(
    frames: Sequence[Frame],
    *,
    source_complete: bool,
    source_reason: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    data_fn: Optional[OcrDataFn] = None,
) -> dict[str, Any]:
    """OCR overlapping More Stats viewports into ordered sections and named rows."""

    if not frames:
        return {
            "source_method": "ocr",
            "page_count": 0,
            "raw_text": "",
            "sections": [],
            "quality": {
                "valid": False,
                "source_complete": False,
                "source_reason": source_reason or "no_frames",
                "source_method": "ocr",
                "warnings": ["No More Stats frames were captured"],
                "retain_source_images": True,
            },
        }

    ocr = data_fn or _default_ocr_data
    pages = [
        _extract_page_lines(
            frame,
            page_index=index,
            data_fn=ocr,
            refine_values=data_fn is None,
        )
        for index, frame in enumerate(frames)
    ]
    occurrences = _section_rows(pages)
    selected = _select_best_rows(occurrences)
    sections = _materialize_sections(selected)
    present_sections = {section["key"] for section in sections}
    missing_sections = sorted(_REQUIRED_MORE_STATS_SECTIONS - present_sections)

    battle_report = next(
        (section for section in sections if section["key"] == "battle_report"),
        None,
    )
    present_required = {
        row["key"] for row in (battle_report or {}).get("rows", [])
    }
    missing_required = sorted(_REQUIRED_BATTLE_REPORT_ROWS - present_required)
    currencies = next(
        (section for section in sections if section["key"] == "currencies"),
        None,
    )
    present_currencies = {
        row["key"] for row in (currencies or {}).get("rows", [])
    }
    missing_currencies = sorted(_REQUIRED_CURRENCY_ROWS - present_currencies)
    uncertain = [
        f"{row['section_key']}.{row['key']}"
        for row in selected
        if float(row["confidence"]) < float(confidence_threshold)
    ]
    unparsed_numeric = [
        f"{row['section_key']}.{row['key']}"
        for row in selected
        if row.get("value_type") == "text"
        and not (
            row.get("section_key") == "battle_report"
            and row.get("key") == "killed_by"
        )
    ]
    warnings: list[str] = []
    if not source_complete:
        warnings.append(f"Long-page capture was incomplete: {source_reason}")
    if missing_sections:
        warnings.append("Missing required More Stats sections: " + ", ".join(missing_sections))
    if missing_required:
        warnings.append("Missing required Battle Report rows: " + ", ".join(missing_required))
    if missing_currencies:
        warnings.append("Missing required Currencies rows: " + ", ".join(missing_currencies))
    if uncertain:
        warnings.append("Low-confidence rows: " + ", ".join(uncertain))
    if unparsed_numeric:
        warnings.append("Unparsed numeric rows: " + ", ".join(unparsed_numeric))

    valid = bool(
        source_complete
        and not missing_sections
        and not missing_required
        and not missing_currencies
        and not uncertain
        and not unparsed_numeric
    )
    raw_pages = []
    for page in pages:
        raw_pages.append(
            f"[viewport {page['page_index'] + 1}]\n"
            + "\n".join(line["text"] for line in page["lines"])
        )

    return {
        "source_method": "ocr",
        "page_count": len(frames),
        "raw_text": "\n\n".join(raw_pages),
        "sections": sections,
        "quality": {
            "valid": valid,
            "source_complete": bool(source_complete),
            "source_reason": source_reason,
            "source_method": "ocr",
            "confidence_threshold": float(confidence_threshold),
            "row_count": len(selected),
            "missing_required_sections": missing_sections,
            "missing_required_rows": missing_required,
            "missing_currency_rows": missing_currencies,
            "low_confidence_rows": uncertain,
            "unparsed_numeric_rows": unparsed_numeric,
            "warnings": warnings,
            "retain_source_images": not valid,
        },
    }


def parse_more_stats_clipboard(text: str) -> dict[str, Any]:
    """Parse the exact tab-delimited Battle Report copied by the Stats UI."""

    raw_text = str(text or "")
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\x00")
    sections: list[dict[str, Any]] = []
    current_section: Optional[dict[str, Any]] = None
    malformed_lines: list[int] = []

    for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
        if not raw_line.strip():
            continue
        if "\t" not in raw_line:
            name = raw_line.strip()
            current_section = {"name": name, "key": _slug(name), "rows": []}
            sections.append(current_section)
            continue

        if current_section is None:
            malformed_lines.append(line_number)
            continue
        label, value = (part.strip() for part in raw_line.split("\t", 1))
        if not label or not value:
            malformed_lines.append(line_number)
            continue

        row = {
            "section": current_section["name"],
            "section_key": current_section["key"],
            "label": label,
            "key": _slug(label),
            "value_raw": value,
            "confidence": 100.0,
            "label_confidence": 100.0,
            "value_confidence": 100.0,
            "source": "android_clipboard",
            "source_line": line_number,
        }
        if current_section["key"] == "battle_report" and row["key"] == "battle_date":
            row.update({"value_type": "datetime_text", "value": value})
        elif current_section["key"] == "killed_with_effect_active":
            effect = _parse_effect_active_line(f"{label} {value}")
            if effect:
                row.update(effect["parsed"])
            else:
                _add_parsed_value(row)
        else:
            _add_parsed_value(row)
        current_section["rows"].append(row)

    present_sections = {section["key"] for section in sections}
    missing_sections = sorted(_REQUIRED_MORE_STATS_SECTIONS - present_sections)
    battle_report = next(
        (section for section in sections if section["key"] == "battle_report"),
        None,
    )
    missing_required = sorted(
        _REQUIRED_BATTLE_REPORT_ROWS
        - {row["key"] for row in (battle_report or {}).get("rows", [])}
    )
    currencies = next(
        (section for section in sections if section["key"] == "currencies"),
        None,
    )
    missing_currencies = sorted(
        _REQUIRED_CURRENCY_ROWS
        - {row["key"] for row in (currencies or {}).get("rows", [])}
    )
    rows = [row for section in sections for row in section["rows"]]
    unparsed_numeric = [
        f"{row['section_key']}.{row['key']}"
        for row in rows
        if row.get("value_type") == "text"
        and not (
            row.get("section_key") == "battle_report"
            and row.get("key") in {"battle_date", "killed_by"}
        )
    ]

    warnings: list[str] = []
    if malformed_lines:
        warnings.append(
            "Malformed clipboard rows at source lines: "
            + ", ".join(str(line) for line in malformed_lines)
        )
    if missing_sections:
        warnings.append("Missing required More Stats sections: " + ", ".join(missing_sections))
    if missing_required:
        warnings.append("Missing required Battle Report rows: " + ", ".join(missing_required))
    if missing_currencies:
        warnings.append("Missing required Currencies rows: " + ", ".join(missing_currencies))
    if unparsed_numeric:
        warnings.append("Unparsed numeric rows: " + ", ".join(unparsed_numeric))

    valid = not (
        malformed_lines
        or missing_sections
        or missing_required
        or missing_currencies
        or unparsed_numeric
    )
    return {
        "source_method": "android_clipboard",
        "page_count": 1,
        "raw_text": raw_text,
        "sections": sections,
        "quality": {
            "valid": valid,
            "source_complete": True,
            "source_reason": "clipboard_copy",
            "source_method": "android_clipboard",
            "row_count": len(rows),
            "missing_required_sections": missing_sections,
            "missing_required_rows": missing_required,
            "missing_currency_rows": missing_currencies,
            "low_confidence_rows": [],
            "unparsed_numeric_rows": unparsed_numeric,
            "malformed_source_lines": malformed_lines,
            "warnings": warnings,
            "retain_source_images": not valid,
        },
    }


def build_battle_record(
    game_stats_frame: Frame,
    more_stats_frames: Sequence[Frame],
    *,
    source_complete: bool,
    source_reason: str,
    battle_id: Optional[str] = None,
    captured_at: Optional[datetime] = None,
    strategy_name: Optional[str] = None,
    run_configuration: Optional[Mapping[str, Any]] = None,
    runtime_context: Optional[Mapping[str, Any]] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    data_fn: Optional[OcrDataFn] = None,
    game_stats_text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """Build a serializable per-battle record from in-memory UI captures."""

    when = captured_at or datetime.now().astimezone()
    game_stats = ocr_game_stats(game_stats_frame, text_fn=game_stats_text_fn)
    more_stats = ocr_more_stats(
        more_stats_frames,
        source_complete=source_complete,
        source_reason=source_reason,
        confidence_threshold=confidence_threshold,
        data_fn=data_fn,
    )
    return _assemble_battle_record(
        game_stats,
        more_stats,
        battle_id=battle_id or make_battle_id(when),
        captured_at=when,
        strategy_name=strategy_name,
        run_configuration=run_configuration,
        runtime_context=runtime_context,
    )


def build_battle_record_from_clipboard(
    game_stats_frame: Frame,
    clipboard_text: str,
    *,
    battle_id: Optional[str] = None,
    captured_at: Optional[datetime] = None,
    strategy_name: Optional[str] = None,
    run_configuration: Optional[Mapping[str, Any]] = None,
    runtime_context: Optional[Mapping[str, Any]] = None,
    game_stats_text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """Build a per-battle record using exact copied Stats text plus Game Stats OCR."""

    when = captured_at or datetime.now().astimezone()
    game_stats = ocr_game_stats(game_stats_frame, text_fn=game_stats_text_fn)
    more_stats = parse_more_stats_clipboard(clipboard_text)
    return _assemble_battle_record(
        game_stats,
        more_stats,
        battle_id=battle_id or make_battle_id(when),
        captured_at=when,
        strategy_name=strategy_name,
        run_configuration=run_configuration,
        runtime_context=runtime_context,
    )


def _assemble_battle_record(
    game_stats: dict[str, Any],
    more_stats: dict[str, Any],
    *,
    battle_id: str,
    captured_at: datetime,
    strategy_name: Optional[str],
    run_configuration: Optional[Mapping[str, Any]],
    runtime_context: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine normalized source data and apply cross-source validation."""

    coin_breakdown = _reconcile_game_coin_breakdown(game_stats, more_stats)
    missing_game_fields = sorted(
        _REQUIRED_GAME_STATS_FIELDS - set(game_stats["fields"])
    )
    game_stats["quality"] = {
        "valid": not missing_game_fields and coin_breakdown["valid"],
        "missing_required_fields": missing_game_fields,
        "coin_breakdown": coin_breakdown,
    }
    runtime = dict(runtime_context or {})
    observed_run_configuration = runtime.pop("observed_run_configuration", None)
    observed_tier = observed_tier_for_record(
        {
            "runtime": runtime,
            "game_stats": game_stats,
            "more_stats": more_stats,
        }
    )
    if observed_tier is not None:
        runtime["observed_tier"] = observed_tier
    classification = analyze_battle_type(
        strategy_name=strategy_name,
        run_configuration=run_configuration,
        terminal_state=runtime.get("terminal_state"),
        record_id=battle_id,
        observed_tier=observed_tier,
        observed_run_configuration=(
            observed_run_configuration
            if isinstance(observed_run_configuration, Mapping)
            else None
        ),
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "battle_id": battle_id,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "battle_type": classification["type"],
        "battle_type_analysis": classification,
        "run_configuration": copy.deepcopy(dict(run_configuration or {})),
        "runtime": runtime,
        "game_stats": game_stats,
        "more_stats": more_stats,
    }
    if isinstance(observed_run_configuration, Mapping):
        record["observed_run_configuration"] = copy.deepcopy(
            dict(observed_run_configuration)
        )
    record["derived"] = derive_battle_stats(record)
    identity = compare_battle_identity(game_stats, more_stats)
    warnings = list(more_stats["quality"]["warnings"])
    if missing_game_fields:
        warnings.append(
            "Missing required Game Stats fields: " + ", ".join(missing_game_fields)
        )
    if not coin_breakdown["valid"]:
        warnings.extend(coin_breakdown["warnings"])
    if identity["mismatches"]:
        warnings.append(
            "Game Stats/More Stats identity mismatch: "
            + ", ".join(item["field"] for item in identity["mismatches"])
        )
    valid = bool(
        more_stats["quality"]["valid"]
        and not missing_game_fields
        and coin_breakdown["valid"]
        and not identity["mismatches"]
    )
    record["quality"] = {
        "valid": valid,
        "retain_source_images": not valid,
        "warnings": warnings,
        "identity": identity,
    }
    return record


def _reconcile_game_coin_breakdown(
    game_stats: dict[str, Any],
    more_stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and repair the compact coin split using the exact copied total.

    The coin icon causes Tesseract to read a trailing ``q``/``Q`` magnitude as
    another zero (for example ``3.00Q`` becomes ``3000``). The copied Battle
    Report supplies the exact total and its case-sensitive suffix, allowing a
    repair only when the resulting base plus ad values agree with that total.
    """

    rows = _row_lookup(more_stats.get("sections", []))
    total_row = rows.get(("battle_report", "coins_earned"))
    copied_total = _row_number(total_row)
    copied_raw = _row_raw(total_row)
    fields = game_stats.get("fields", {})
    base_field = fields.get("base_coins_earned")
    ad_field = fields.get("ad_coins_earned")
    if copied_total is None or base_field is None or ad_field is None:
        return {
            "valid": False,
            "reconciled": False,
            "copied_total_raw": copied_raw,
            "warnings": ["Compact Game Stats coin breakdown could not be validated"],
        }

    base = _field_decimal(base_field)
    ad = _field_decimal(ad_field)
    if base is not None and ad is not None and _approximately_total(base + ad, copied_total):
        return {
            "valid": True,
            "reconciled": False,
            "copied_total_raw": copied_raw,
            "warnings": [],
        }

    suffix_match = re.search(r"([KMBTqQsSOND]|[a-z]{2})$", copied_raw.strip())
    if not suffix_match:
        return {
            "valid": False,
            "reconciled": False,
            "copied_total_raw": copied_raw,
            "warnings": ["Compact Game Stats coin breakdown disagrees with copied total"],
        }
    suffix = suffix_match.group(1)
    repaired: dict[str, tuple[str, Decimal]] = {}
    for key in ("base_coins_earned", "ad_coins_earned", "total_coins_earned"):
        field = fields.get(key)
        if not field:
            continue
        candidate = _repair_coin_suffix(str(field.get("raw") or ""), suffix)
        value = parse_tower_number(candidate) if candidate else None
        if candidate and value is not None:
            repaired[key] = (candidate, value)

    if not {"base_coins_earned", "ad_coins_earned"} <= repaired.keys():
        return {
            "valid": False,
            "reconciled": False,
            "copied_total_raw": copied_raw,
            "warnings": ["Compact Game Stats coin magnitudes could not be recovered"],
        }
    repaired_sum = repaired["base_coins_earned"][1] + repaired["ad_coins_earned"][1]
    if not _approximately_total(repaired_sum, copied_total):
        return {
            "valid": False,
            "reconciled": False,
            "copied_total_raw": copied_raw,
            "warnings": ["Repaired Game Stats coin split disagrees with copied total"],
        }

    for key, (raw, value) in repaired.items():
        field = fields[key]
        field["ocr_raw"] = field.get("raw")
        field["raw"] = raw
        field["decimal"] = str(value)
        field["reconciled_from_copied_total"] = True
    return {
        "valid": True,
        "reconciled": True,
        "copied_total_raw": copied_raw,
        "suffix": suffix,
        "warnings": [],
    }


def _repair_coin_suffix(raw: str, suffix: str) -> Optional[str]:
    text = (raw or "").strip().replace(",", "")
    if re.search(r"([KMBTqQsSOND]|[a-z]{2})$", text):
        return text
    if not text.endswith("0"):
        return None
    mantissa = text[:-1]
    if "." not in mantissa:
        if len(mantissa) < 3:
            return None
        mantissa = mantissa[:-2] + "." + mantissa[-2:]
    return mantissa + suffix


def _approximately_total(value: Decimal, expected: Decimal) -> bool:
    tolerance = max(expected.copy_abs() * Decimal("0.02"), Decimal("0.01"))
    return (value - expected).copy_abs() <= tolerance


def compare_battle_identity(
    game_stats: Mapping[str, Any],
    more_stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare stable battle identifiers exposed by both source surfaces."""

    fields = game_stats.get("fields", {})
    rows = _row_lookup(more_stats.get("sections", []))
    report = {
        row_key: row
        for (section_key, row_key), row in rows.items()
        if section_key == "battle_report"
    }
    checked: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for field in ("wave", "tier", "killed_by"):
        game_value = fields.get(field, {}).get("value")
        report_value = report.get(field, {}).get("value")
        if game_value is None or report_value is None:
            continue
        checked.append(field)
        left = str(game_value).strip().casefold()
        right = str(report_value).strip().casefold()
        if left != right:
            mismatches.append(
                {
                    "field": field,
                    "game_stats": game_value,
                    "more_stats": report_value,
                }
            )
    return {
        "checked_fields": checked,
        "mismatches": mismatches,
        "valid": not mismatches,
    }


def attach_battle_perks(
    record: dict[str, Any],
    perks: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach ordered Selected Perks and fold their validation into the record."""

    record["perks"] = dict(perks)
    perk_quality = perks.get("quality", {})
    if perk_quality.get("valid"):
        return record
    warnings = list(perk_quality.get("warnings", [])) or ["Perks capture failed validation"]
    record["quality"]["valid"] = False
    record["quality"]["retain_source_images"] = True
    record["quality"]["warnings"].extend(warnings)
    return record


def attach_observed_run_configuration(
    record: dict[str, Any],
    observed_run_configuration: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach actual-value evidence and refresh evidence-based run identity."""

    observed = copy.deepcopy(dict(observed_run_configuration))
    record["observed_run_configuration"] = observed
    runtime = record.get("runtime")
    terminal_state = runtime.get("terminal_state") if isinstance(runtime, Mapping) else None
    configured = record.get("run_configuration")
    classification = analyze_battle_type(
        strategy_name=record.get("strategy"),
        run_configuration=(configured if isinstance(configured, Mapping) else {}),
        terminal_state=terminal_state,
        record_id=str(record.get("battle_id") or ""),
        observed_tier=observed_tier_for_record(record),
        observed_run_configuration=observed,
    )
    record["battle_type"] = classification["type"]
    record["battle_type_analysis"] = classification
    return record


def derive_battle_stats(record: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate useful per-battle values that the in-game page omits."""

    rows = _row_lookup(record.get("more_stats", {}).get("sections", []))
    game_fields = record.get("game_stats", {}).get("fields", {})

    wave = _row_int(rows.get(("battle_report", "wave")))
    game_seconds = parse_duration_seconds(_row_raw(rows.get(("battle_report", "game_time"))))
    real_seconds = parse_duration_seconds(_row_raw(rows.get(("battle_report", "real_time"))))
    coins = _row_number(rows.get(("battle_report", "coins_earned")))
    cells = _row_number(rows.get(("battle_report", "cells_earned")))

    derived: dict[str, Any] = {}
    if game_seconds and real_seconds:
        derived["effective_game_speed"] = round(game_seconds / real_seconds, 4)
    if wave and real_seconds:
        derived["waves_per_real_hour"] = round(wave * 3600 / real_seconds, 3)
        derived["real_seconds_per_wave"] = round(real_seconds / wave, 4)
    if wave and coins is not None:
        derived["coins_per_wave_decimal"] = str(coins / Decimal(wave))
    if wave and cells is not None:
        derived["cells_per_wave_decimal"] = str(cells / Decimal(wave))

    if real_seconds:
        currency_rates = _currency_rates_per_real_hour(
            rows,
            real_seconds=real_seconds,
        )
        if currency_rates:
            derived["currency_rates_per_real_hour"] = currency_rates

        # The reroll currency is labelled "Reroll Shards" in the row text and
        # represented by a die in the UI. Combine normal and fetched shards for
        # the requested overall Dice/hour rate.
        reroll_dice = _sum_rows_by_key(
            rows,
            "reroll_shards_earned",
            "reroll_shards_fetched",
            require_all=True,
        )
        if reroll_dice is not None:
            derived["reroll_dice_per_real_hour_decimal"] = str(
                _per_real_hour(reroll_dice, real_seconds)
            )

        total_module_shards = _sum_rows_by_key(
            rows,
            "cannon_shards",
            "armor_shards",
            "generator_shards",
            "core_shards",
            require_all=True,
        )
        if total_module_shards is not None:
            derived["module_shards_per_real_hour_decimal"] = str(
                _per_real_hour(total_module_shards, real_seconds)
            )

    base_coins = _field_decimal(game_fields.get("base_coins_earned"))
    ad_coins = _field_decimal(game_fields.get("ad_coins_earned"))
    total_coins = _row_number(rows.get(("battle_report", "coins_earned")))
    if total_coins is None:
        total_coins = _field_decimal(game_fields.get("total_coins_earned"))
    if total_coins and base_coins is not None:
        derived["base_coin_share_percent"] = round(float(base_coins / total_coins * 100), 3)
    if total_coins and ad_coins is not None:
        derived["ad_coin_share_percent"] = round(float(ad_coins / total_coins * 100), 3)

    death_defies = _row_int(rows.get(("counts", "death_defy")))
    if death_defies is None:
        death_defies = game_fields.get("death_defies", {}).get("value")
    if death_defies is not None:
        derived["death_defies"] = int(death_defies)

    captured_at = record.get("captured_at")
    if captured_at and real_seconds:
        try:
            end = datetime.fromisoformat(str(captured_at))
            derived["estimated_started_at"] = (
                end - timedelta(seconds=real_seconds)
            ).isoformat(timespec="seconds")
        except ValueError:
            pass

    runtime_wave = record.get("runtime", {}).get("last_wave")
    if wave is not None and isinstance(runtime_wave, int):
        derived["runtime_wave_error"] = runtime_wave - wave
    return derived


def persist_battle_record(
    record: Mapping[str, Any],
    *,
    records_dir: Path | str = DEFAULT_RECORDS_DIR,
) -> tuple[Path, Path]:
    """Atomically persist pretty JSON plus a human-readable Markdown view."""

    directory = Path(records_dir)
    directory.mkdir(parents=True, exist_ok=True)
    battle_id = str(record["battle_id"])
    json_path = directory / f"{battle_id}.json"
    markdown_path = directory / f"{battle_id}.md"
    _atomic_write(json_path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    _atomic_write(markdown_path, render_battle_markdown(record))
    return json_path, markdown_path


def render_battle_markdown(record: Mapping[str, Any]) -> str:
    """Render one battle record for quick perusal without losing OCR evidence."""

    lines = [f"# {record.get('battle_id', 'Battle')}", ""]
    lines.append(f"Captured: {record.get('captured_at', 'unknown')}")
    if record.get("strategy"):
        lines.append(f"Strategy: {record['strategy']}")
    classification = record.get("battle_type_analysis") or {}
    if record.get("battle_type"):
        confidence = classification.get("confidence")
        suffix = f" ({confidence} confidence)" if confidence else ""
        lines.append(f"Battle type: {str(record['battle_type']).title()}{suffix}")
    observed_tier = observed_tier_for_record(record)
    if observed_tier is not None:
        lines.append(f"Observed tier: {observed_tier}")
    run_configuration = record.get("run_configuration") or {}
    if run_configuration:
        lines.append(
            "Run configuration: "
            f"{run_configuration.get('profile', 'unknown')} "
            f"Tier {run_configuration.get('tier', 'unknown')}"
        )
    source_method = record.get("more_stats", {}).get("source_method", "ocr")
    lines.append(f"Stats source: {_display_source_method(str(source_method))}")
    if run_configuration:
        lines.extend(["", "## Run Configuration", ""])
        settings = run_configuration.get("settings") or {}
        if settings:
            lines.extend(["### Profile Settings", ""])
            for key, label in (
                ("cards_deck", "Cards deck"),
                ("workshop_preset", "Workshop preset"),
                ("bots_preset", "Bots preset"),
                ("guardian_chips", "Guardian chips"),
                ("auto_pick_perks", "Auto Pick Perks"),
            ):
                if key not in settings:
                    continue
                value = settings[key]
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    rendered = " > ".join(str(item) for item in value)
                elif isinstance(value, bool):
                    rendered = "enabled" if value else "disabled"
                else:
                    rendered = str(value)
                lines.append(f"- {label}: {rendered}")
            ultimate_weapons = settings.get("ultimate_weapons")
            if isinstance(ultimate_weapons, Mapping):
                lines.append("- Ultimate Weapons:")
                for weapon, toggles in ultimate_weapons.items():
                    if isinstance(toggles, Mapping):
                        rendered = ", ".join(
                            f"{name}={value}" for name, value in toggles.items()
                        )
                    else:
                        rendered = str(toggles)
                    lines.append(f"  - {weapon}: {rendered}")

        loadout = run_configuration.get("loadout") or {}
        lines.extend(["", "### Loadout", ""])
        for key, label in (
            ("modules", "Modules"),
            ("damage_slider", "Damage Slider"),
            ("orb_distance", "Orb Distance"),
            ("target_priority", "Target Priority"),
        ):
            policy = loadout.get(key)
            if not isinstance(policy, Mapping):
                continue
            details = [f"mode `{policy.get('mode', 'unknown')}`"]
            if policy.get("preset"):
                details.append(f"preset `{policy['preset']}`")
            if "value" in policy:
                details.append(f"value `{policy['value']}`")
            resolved = policy.get("resolved")
            if isinstance(resolved, Mapping):
                values = "; ".join(
                    f"{name}={value}" for name, value in resolved.items()
                )
                details.append(f"resolved: {values}")
            elif isinstance(resolved, Sequence) and not isinstance(
                resolved,
                (str, bytes),
            ):
                details.append(
                    "resolved: " + " > ".join(str(value) for value in resolved)
                )
            lines.append(f"- {label}: " + "; ".join(details))

    observed_configuration = record.get("observed_run_configuration") or {}
    if isinstance(observed_configuration, Mapping) and observed_configuration:
        lines.extend(["", "## Observed Run Configuration", ""])
        coverage = observed_configuration.get("coverage") or {}
        if isinstance(coverage, Mapping):
            lines.append(
                "Coverage: "
                f"{coverage.get('observed', 0)} interpreted + "
                f"{coverage.get('evidence_captured', 0)} raw-evidence + "
                f"{coverage.get('unavailable', 0)} unavailable / "
                f"{coverage.get('total', 0)} fields"
            )
        fields = observed_configuration.get("fields") or {}
        labels = {
            "run_identity": "Run identity",
            "cards_deck": "Cards deck",
            "workshop_preset": "Workshop preset",
            "free_upgrade_locks": "Free Upgrade locks",
            "bots_preset": "Bots preset",
            "guardian_chips": "Guardian chips",
            "modules": "Modules",
            "target_priority": "Target Priority",
            "damage_slider": "Damage Slider",
            "auto_pick_perks": "Auto Pick Perks",
            "perk_first_choice": "First Perk",
            "perk_bans": "Banned Perks",
            "perk_auto_pick_order": "Auto Pick order",
            "ultimate_weapons": "Ultimate Weapons",
        }
        not_observed = []
        if isinstance(fields, Mapping):
            for key, label in labels.items():
                evidence = fields.get(key)
                if not isinstance(evidence, Mapping):
                    not_observed.append(label)
                    continue
                status = evidence.get("status")
                if status == "evidence_captured":
                    value = evidence.get("value")
                    paths = value.get("evidence_images") if isinstance(value, Mapping) else None
                    rendered_paths = ", ".join(str(path) for path in paths or ())
                    lines.append(
                        f"- {label}: raw evidence captured; structured interpretation "
                        f"pending ({rendered_paths or 'no image path'})"
                    )
                    continue
                if status == "unavailable":
                    reason = evidence.get("reason") or "control unavailable"
                    phase = str(evidence.get("phase") or "unknown phase").replace(
                        "_", " "
                    )
                    lines.append(f"- {label}: unavailable ({reason}; {phase})")
                    continue
                if status != "observed":
                    not_observed.append(label)
                    continue
                value = _render_observed_value(key, evidence.get("value"))
                phase = str(evidence.get("phase") or "unknown phase").replace("_", " ")
                observed_at = evidence.get("observed_at") or "unknown time"
                confidence = evidence.get("confidence") or "unknown confidence"
                lines.append(
                    f"- {label}: {value} "
                    f"({confidence}; {phase}; {observed_at})"
                )
        if not_observed:
            lines.append("- Not observed: " + ", ".join(not_observed))
    lines.extend(["", "## Derived", ""])
    derived = record.get("derived", {})
    if derived:
        for key, value in derived.items():
            if key == "currency_rates_per_real_hour":
                continue
            lines.append(
                f"- {_display_derived_key(key)}: {_display_derived(key, value)}"
            )
    else:
        lines.append("No derived values were available.")

    currency_rates = derived.get("currency_rates_per_real_hour", {})
    if currency_rates:
        lines.extend(["", "### Currency rates", ""])
        for rate in currency_rates.values():
            lines.append(
                f"- {rate['label']}/hour: "
                f"{format_tower_number(Decimal(str(rate['value_decimal'])))}"
            )

    lines.extend(
        render_coin_rate_samples_markdown(
            record.get("runtime", {}).get("coin_rate_samples", [])
        )
    )
    lines.extend(
        render_survival_ability_activations_markdown(
            record.get("runtime", {}).get("survival_ability_activations", {})
        )
    )

    game_fields = record.get("game_stats", {}).get("fields", {})
    lines.extend(["", "## Game Stats-only fields", "", "| Stat | Value |", "| --- | ---: |"])
    for key in ("highest_wave", "death_defies", "base_coins_earned", "ad_coins_earned"):
        field = game_fields.get(key)
        if field:
            lines.append(f"| {_display_key(key)} | {field.get('raw', field.get('value', ''))} |")

    perks = record.get("perks", {})
    selected_perks = perks.get("selected", [])
    if perks:
        lines.extend(["", "## Selected Perks", ""])
        lines.append(
            "Order is latest selection first; a blue leveled perk moves to the "
            "front when its newest level is selected."
        )
        lines.extend(
            [
                "",
                "| Rank | Color | Instance model | Displayed perk | OCR confidence |",
                "| ---: | --- | --- | --- | ---: |",
            ]
        )
        for perk in selected_perks:
            lines.append(
                f"| {perk['latest_selection_rank']} | {perk['color']} | "
                f"{perk['instance_model']} | {perk['display_text']} | "
                f"{float(perk['confidence']):.1f} |"
            )

    for section in record.get("more_stats", {}).get("sections", []):
        lines.extend(
            [
                "",
                f"## {section['name']}",
                "",
                "| Stat | Value | Source quality |",
                "| --- | ---: | ---: |",
            ]
        )
        for row in section.get("rows", []):
            lines.append(
                f"| {row['label']} | {row['value_raw']} | {_display_row_quality(row)} |"
            )

    quality = record.get("quality", {})
    lines.extend(["", "## Capture quality", ""])
    lines.append(f"Valid: {'yes' if quality.get('valid') else 'no'}")
    for warning in quality.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


def render_coin_rate_samples_markdown(samples: Any) -> list[str]:
    """Render numeric during-run Coins/min observations as a report table."""

    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
        return []
    valid_samples = [sample for sample in samples if isinstance(sample, Mapping)]
    if not valid_samples:
        return []

    lines = [
        "",
        "## Coins/min progression",
        "",
        "| Captured | Wave | Coins/min | OCR confidence |",
        "| --- | ---: | ---: | ---: |",
    ]
    for sample in valid_samples:
        rate = sample.get("display")
        if not rate:
            try:
                rate = format_tower_number(
                    Decimal(str(sample.get("coins_per_minute_decimal")))
                )
            except Exception:
                rate = ""
        confidence = sample.get("confidence")
        confidence_text = f"{float(confidence):.1f}%" if confidence is not None else ""
        lines.append(
            f"| {sample.get('captured_at', '')} | {sample.get('wave', '')} | "
            f"{rate} | {confidence_text} |"
        )
    return lines


def render_survival_ability_activations_markdown(
    observations: Any,
) -> list[str]:
    """Render approximate Demon Mode and Nuke activation waves."""

    if not isinstance(observations, Mapping):
        return []
    demon = observations.get("demon_mode_first_activation")
    raw_nukes = observations.get("nuke_activations")
    nukes = (
        [event for event in raw_nukes if isinstance(event, Mapping)]
        if isinstance(raw_nukes, Sequence)
        and not isinstance(raw_nukes, (str, bytes))
        else []
    )
    if not isinstance(demon, Mapping) and not nukes:
        return []

    lines = ["", "## Survival ability activations", ""]
    if isinstance(demon, Mapping):
        lines.append(
            "- Demon Mode first activation: "
            + _render_activation_wave(demon)
        )
    else:
        lines.append("- Demon Mode first activation: not observed")
    if nukes:
        lines.extend(
            [
                "",
                "| Nuke activation | Approximate wave | Detected |",
                "| ---: | ---: | --- |",
            ]
        )
        for index, event in enumerate(nukes, start=1):
            sequence = event.get("sequence", index)
            wave = event.get("approximate_wave")
            lines.append(
                f"| {sequence} | {wave if wave is not None else 'unknown'} | "
                f"{event.get('detected_at', '')} |"
            )
    else:
        lines.append("- Nuke activations: none observed")
    return lines


def _render_activation_wave(event: Mapping[str, Any]) -> str:
    wave = event.get("approximate_wave")
    wave_text = str(wave) if wave is not None else "unknown wave"
    detected_at = event.get("detected_at")
    if detected_at:
        return f"approximately wave {wave_text} (detected {detected_at})"
    return f"approximately wave {wave_text}"


def _default_ocr_data(frame: Frame) -> Mapping[str, Sequence[Any]]:
    if pytesseract is None:
        raise RuntimeError("pytesseract is unavailable")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return pytesseract.image_to_data(
        rgb,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )


def _extract_page_lines(
    frame: Frame,
    *,
    page_index: int,
    data_fn: OcrDataFn,
    refine_values: bool,
) -> dict[str, Any]:
    x_offset, y_offset, _, _ = MORE_STATS_CROP
    crop = _crop(frame, MORE_STATS_CROP)
    data = data_fn(crop)
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    texts = data.get("text", [])
    count = len(texts)
    for index in range(count):
        text = str(texts[index] or "").strip()
        if not text:
            continue
        confidence = _float_at(data, "conf", index, -1.0)
        if confidence < 0:
            continue
        token = {
            "text": text,
            "confidence": confidence,
            "left": x_offset + _int_at(data, "left", index),
            "top": y_offset + _int_at(data, "top", index),
            "width": _int_at(data, "width", index),
            "height": _int_at(data, "height", index),
        }
        key = (
            _int_at(data, "block_num", index),
            _int_at(data, "par_num", index),
            _int_at(data, "line_num", index),
        )
        groups[key].append(token)

    lines: list[dict[str, Any]] = []
    for tokens in groups.values():
        tokens.sort(key=lambda token: token["left"])
        line_top = min(token["top"] for token in tokens)
        line_height = max(token["height"] for token in tokens)
        if line_top < SAFE_LINE_TOP or line_top + line_height > SAFE_LINE_BOTTOM:
            # Small overlapping swipes guarantee another complete observation.
            # Discard text clipped by the modal's viewport edges before it can
            # become a bogus section or beat a complete duplicate.
            continue
        label_tokens = [token for token in tokens if token["left"] < VALUE_COLUMN_X]
        value_tokens = [token for token in tokens if token["left"] >= VALUE_COLUMN_X]
        text = " ".join(token["text"] for token in tokens)
        label = " ".join(token["text"] for token in label_tokens)
        value = " ".join(token["text"] for token in value_tokens)
        lines.append(
            {
                "text": text,
                "label": label,
                "value": value,
                "left": min(token["left"] for token in tokens),
                "top": line_top,
                "height": line_height,
                "confidence": _mean_conf(tokens),
                "label_confidence": _mean_conf(label_tokens),
                "value_confidence": _mean_conf(value_tokens),
            }
        )
    lines.sort(key=lambda line: (line["top"], line["left"]))
    if refine_values:
        _refine_value_columns(crop, lines, x_offset=x_offset, y_offset=y_offset)
    return {"page_index": page_index, "lines": lines}


def _refine_value_columns(
    crop: Frame,
    lines: Sequence[dict[str, Any]],
    *,
    x_offset: int,
    y_offset: int,
) -> None:
    """Use sparse-text OCR to improve weak values while preserving row layout."""

    if pytesseract is None:
        return
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(
        rgb,
        config="--psm 11",
        output_type=pytesseract.Output.DICT,
    )
    alternatives: list[dict[str, Any]] = []
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text or "").strip()
        left = x_offset + _int_at(data, "left", index)
        confidence = _float_at(data, "conf", index, -1.0)
        if not text or left < VALUE_COLUMN_X or confidence < 0:
            continue
        top = y_offset + _int_at(data, "top", index)
        height = _int_at(data, "height", index)
        alternatives.append(
            {
                "text": text,
                "confidence": confidence,
                "left": left,
                "center": top + height / 2.0,
            }
        )

    for line in lines:
        if not line["value"]:
            continue
        center = float(line["top"]) + float(line["height"]) / 2.0
        matching = [
            token for token in alternatives if abs(float(token["center"]) - center) <= 18.0
        ]
        if matching:
            matching.sort(key=lambda token: token["left"])
            confidence = _mean_conf(matching)
            if confidence > float(line["value_confidence"]):
                line["value"] = " ".join(token["text"] for token in matching)
                line["value_confidence"] = confidence
                line["text"] = f"{line['label']} {line['value']}".strip()

        if float(line["label_confidence"]) < DEFAULT_CONFIDENCE_THRESHOLD:
            refined_label = _ocr_line_column(
                crop,
                line,
                x_start=0,
                x_end=VALUE_COLUMN_X - x_offset,
                y_offset=y_offset,
                cyan_only=False,
            )
            if refined_label and refined_label[1] > float(line["label_confidence"]):
                line["label"], line["label_confidence"] = refined_label
                line["text"] = f"{line['label']} {line['value']}".strip()

        if _needs_targeted_value_ocr(
            str(line["value"]),
            float(line["value_confidence"]),
        ):
            refined_value = _ocr_line_column(
                crop,
                line,
                x_start=VALUE_COLUMN_X - x_offset - 20,
                x_end=crop.shape[1],
                y_offset=y_offset,
                cyan_only=True,
            )
            if refined_value and _prefer_refined_value(
                current=str(line["value"]),
                current_confidence=float(line["value_confidence"]),
                candidate=refined_value[0],
                candidate_confidence=refined_value[1],
            ):
                line["value"], line["value_confidence"] = refined_value
                line["text"] = f"{line['label']} {line['value']}".strip()


def _ocr_line_column(
    crop: Frame,
    line: Mapping[str, Any],
    *,
    x_start: int,
    x_end: int,
    y_offset: int,
    cyan_only: bool,
) -> Optional[tuple[str, float]]:
    if pytesseract is None:
        return None
    padding = 20
    top = max(0, int(line["top"]) - y_offset - padding)
    bottom = min(
        crop.shape[0],
        int(line["top"]) - y_offset + int(line["height"]) + padding,
    )
    column = crop[top:bottom, max(0, x_start):max(0, x_end)]
    if column.size == 0:
        return None
    if cyan_only:
        hsv = cv2.cvtColor(column, cv2.COLOR_BGR2HSV)
        column = cv2.inRange(hsv, (75, 80, 100), (105, 255, 255))
    candidates: list[tuple[str, float]] = []
    for psm in (6, 7):
        data = pytesseract.image_to_data(
            column,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
        )
        tokens: list[str] = []
        confidences: list[float] = []
        for index, raw_text in enumerate(data.get("text", [])):
            text = str(raw_text or "").strip().strip("_—")
            confidence = _float_at(data, "conf", index, -1.0)
            if not text or confidence < 0 or not re.search(r"[A-Za-z0-9]", text):
                continue
            tokens.append(text)
            confidences.append(confidence)
        if tokens:
            candidates.append(
                (" ".join(tokens), sum(confidences) / len(confidences))
            )
    return max(candidates, key=lambda item: item[1]) if candidates else None


def _needs_targeted_value_ocr(value: str, confidence: float) -> bool:
    normalized = _normalize_ocr_value(value)
    if confidence < DEFAULT_CONFIDENCE_THRESHOLD:
        return True
    if re.match(r"[$]?\s*[0-9]", normalized) and parse_tower_number(normalized) is None:
        return True
    return bool(re.fullmatch(r"[0-9]+\.[0-9]+0", normalized))


def _prefer_refined_value(
    *,
    current: str,
    current_confidence: float,
    candidate: str,
    candidate_confidence: float,
) -> bool:
    current_number = parse_tower_number(_normalize_ocr_value(current))
    candidate_number = parse_tower_number(_normalize_ocr_value(candidate))
    if candidate_number is None:
        return False
    if current_number is None or candidate_confidence > current_confidence:
        return True
    current_suffix = re.search(r"([KMBTqQsSOND]|[a-z]{2})$", current.strip())
    candidate_suffix = re.search(r"([KMBTqQsSOND]|[a-z]{2})$", candidate.strip())
    return bool(candidate_suffix and not current_suffix)


def _section_rows(pages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    prior_sections: dict[tuple[str, str], set[str]] = defaultdict(set)
    prior_label_sections: dict[str, set[str]] = defaultdict(set)
    previous_last_section = "unsectioned"
    previous_last_section_name = "Unsectioned"

    for page in pages:
        lines = list(page["lines"])
        exact_votes: Counter[str] = Counter()
        label_votes: Counter[str] = Counter()
        for line in lines:
            if _is_section_header(line):
                break
            if line["label"] and line["value"]:
                match_key = (_slug(line["label"]), _normalize_value(line["value"]))
                exact_sections = prior_sections.get(match_key, set())
                label_sections = prior_label_sections.get(_slug(line["label"]), set())
                for section in exact_sections:
                    exact_votes[section] += 1
                for section in label_sections:
                    label_votes[section] += 1
        inherited_votes = exact_votes or label_votes
        current_section = (
            inherited_votes.most_common(1)[0][0]
            if inherited_votes
            else previous_last_section
        )
        current_section_name = (
            _display_key(current_section)
            if inherited_votes
            else previous_last_section_name
        )

        for line in lines:
            if _is_section_header(line):
                current_section_name = line["text"].strip()
                current_section = _slug(current_section_name)
                continue
            if not line["label"] or not line["value"]:
                continue
            label = line["label"].strip()
            value = line["value"].strip()
            special_value: Optional[dict[str, Any]] = None
            if current_section == "battle_report":
                battle_date = _parse_battle_date_line(str(line["text"]))
                if battle_date:
                    label, value = "Battle Date", battle_date
                    special_value = {
                        "value_type": "datetime_text",
                        "value": battle_date,
                    }
            elif current_section == "killed_with_effect_active":
                effect = _parse_effect_active_line(str(line["text"]))
                if effect:
                    label = effect["label"]
                    value = effect["value_raw"]
                    special_value = effect["parsed"]
            row = {
                "section": current_section_name,
                "section_key": current_section,
                "label": label,
                "key": _slug(label),
                "value_raw": value,
                "confidence": round(
                    min(line["label_confidence"], line["value_confidence"]),
                    1,
                ),
                "label_confidence": round(line["label_confidence"], 1),
                "value_confidence": round(line["value_confidence"], 1),
                "viewport": int(page["page_index"]) + 1,
                "top": int(line["top"]),
            }
            if special_value:
                row.update(special_value)
            else:
                _add_parsed_value(row)
            occurrences.append(row)
            prior_sections[(_slug(label), _normalize_value(value))].add(current_section)
            prior_label_sections[_slug(label)].add(current_section)
        previous_last_section = current_section
        previous_last_section_name = current_section_name
    return occurrences


def _select_best_rows(occurrences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for occurrence in occurrences:
        key = (str(occurrence["section_key"]), str(occurrence["key"]))
        candidate = dict(occurrence)
        if key not in chosen:
            chosen[key] = candidate
            order.append(key)
            continue
        existing = chosen[key]
        if _row_score(candidate) > _row_score(existing):
            chosen[key] = candidate
    return [chosen[key] for key in order]


def _materialize_sections(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for source_row in rows:
        row = dict(source_row)
        section_key = str(row["section_key"])
        if section_key not in by_key:
            section = {
                "name": row["section"],
                "key": section_key,
                "rows": [],
            }
            by_key[section_key] = section
            sections.append(section)
        by_key[section_key]["rows"].append(row)
    return sections


def _is_section_header(line: Mapping[str, Any]) -> bool:
    text = str(line.get("text") or "").strip()
    if not text:
        return False
    if _slug(text) in {"round_stats"}:
        return False
    return int(line.get("left") or 0) >= 280


def _parse_battle_date_line(text: str) -> Optional[str]:
    match = re.fullmatch(r"Battle\s+Date\s+(.+)", (text or "").strip(), re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_effect_active_line(text: str) -> Optional[dict[str, Any]]:
    """Split the count and optional active percentage from a three-column row."""

    normalized = re.sub(r"\b[0Oo]\)(?=$|\s)", "0", (text or "").strip())
    match = re.fullmatch(
        r"(.+?)\s+"
        r"([0-9]+(?:\.[0-9]+)?(?:[KMBTqQsSOND]|[a-z]{2})?)"
        r"(?:\s+(\[[0-9]+(?:\.[0-9]+)?%\]?))?",
        normalized,
    )
    if not match:
        return None
    count_raw = match.group(2)
    count = parse_tower_number(count_raw)
    if count is None:
        return None

    percent_raw = match.group(3)
    value_raw = count_raw + (f" {percent_raw}" if percent_raw else "")
    parsed: dict[str, Any] = {
        "value_type": "count_with_active_percent" if percent_raw else "tower_number",
        "value_decimal": str(count),
    }
    if count == count.to_integral_value() and count.adjusted() < 18:
        parsed["value"] = int(count)
    if percent_raw:
        percent_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", percent_raw)
        if percent_match:
            parsed["active_percent"] = float(percent_match.group(1))
    return {
        "label": match.group(1).strip(),
        "value_raw": value_raw,
        "parsed": parsed,
    }


def _add_parsed_value(row: dict[str, Any]) -> None:
    raw = str(row["value_raw"])
    normalized = _normalize_ocr_value(raw)
    if normalized != raw:
        row["value_normalized"] = normalized
    duration = parse_duration_seconds(normalized)
    if duration is not None:
        row["value_type"] = "duration_seconds"
        row["value"] = duration
        return
    number = parse_tower_number(normalized)
    if number is not None:
        row["value_type"] = "tower_number"
        row["value_decimal"] = str(number)
        if number == number.to_integral_value() and number.adjusted() < 18:
            row["value"] = int(number)
        return
    multiplier = re.fullmatch(r"[xX]\s*([0-9]+(?:\.[0-9]+)?)", normalized)
    if multiplier:
        row["value_type"] = "multiplier"
        row["value"] = float(multiplier.group(1))
        return
    percentage = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*%", normalized)
    if percentage:
        row["value_type"] = "percent"
        row["value"] = float(percentage.group(1))
        return
    minimum_integer = re.fullmatch(r"([0-9]+)\s*\+", normalized)
    if minimum_integer:
        row["value_type"] = "minimum_integer"
        row["value"] = int(minimum_integer.group(1))
        return
    row["value_type"] = "text"
    row["value"] = raw


def _row_score(row: Mapping[str, Any]) -> tuple[float, float, int]:
    raw = _normalize_ocr_value(str(row.get("value_raw") or ""))
    parsed_bonus = int(
        parse_tower_number(raw) is not None
        or parse_duration_seconds(raw) is not None
    )
    return (
        float(row.get("confidence") or -1.0),
        float(row.get("value_confidence") or -1.0),
        parsed_bonus,
    )


def _row_lookup(sections: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for section in sections:
        section_key = str(section.get("key") or "")
        for row in section.get("rows", []):
            lookup[(section_key, str(row.get("key") or ""))] = row
    return lookup


def _first_row_by_key(
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    *keys: str,
) -> Optional[Mapping[str, Any]]:
    """Find a uniquely named stat without coupling a derived value to its section."""

    for key in keys:
        for (_section_key, row_key), row in rows.items():
            if row_key == key:
                return row
    return None


def _row_number(row: Optional[Mapping[str, Any]]) -> Optional[Decimal]:
    """Return the row's normalized parsed number, falling back to raw OCR."""

    if not row:
        return None
    decimal_value = row.get("value_decimal")
    if decimal_value is not None:
        try:
            return Decimal(str(decimal_value))
        except (InvalidOperation, TypeError, ValueError):
            pass
    normalized = str(row.get("value_normalized") or _row_raw(row))
    return parse_tower_number(normalized)


def _sum_rows_by_key(
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    *keys: str,
    require_all: bool = False,
) -> Optional[Decimal]:
    """Sum named numeric rows, optionally requiring every requested row."""

    values: list[Decimal] = []
    for key in keys:
        row = _first_row_by_key(rows, key)
        value = _row_number(row)
        if value is None:
            if require_all:
                return None
            continue
        values.append(value)
    return sum(values, Decimal(0)) if values else None


def _currency_rates_per_real_hour(
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    real_seconds: int,
) -> dict[str, dict[str, str]]:
    """Calculate rates for Currencies rows lacking an in-game hourly peer."""

    all_row_keys = {row_key for _section_key, row_key in rows}
    rates: dict[str, dict[str, str]] = {}
    for (section_key, row_key), row in rows.items():
        if section_key != "currencies" or row_key.endswith("_per_hour"):
            continue
        stem = re.sub(r"_(?:earned|fetched|tapped)$", "", row_key)
        existing_rate_keys = {
            f"{row_key}_per_hour",
            f"{stem}_per_hour",
        }
        if all_row_keys.intersection(existing_rate_keys):
            continue
        value = _row_number(row)
        if value is None:
            continue
        rates[row_key] = {
            "label": str(row.get("label") or _display_key(row_key)),
            "source_raw": _row_raw(row),
            "value_decimal": str(_per_real_hour(value, real_seconds)),
        }
    return rates


def _per_real_hour(value: Decimal, real_seconds: int) -> Decimal:
    return value * Decimal(3600) / Decimal(real_seconds)


def _row_raw(row: Optional[Mapping[str, Any]]) -> str:
    return str((row or {}).get("value_raw") or "")


def _row_int(row: Optional[Mapping[str, Any]]) -> Optional[int]:
    if not row:
        return None
    value = row.get("value")
    return int(value) if isinstance(value, int) else None


def _field_decimal(field: Optional[Mapping[str, Any]]) -> Optional[Decimal]:
    if not field or field.get("decimal") is None:
        return None
    try:
        return Decimal(str(field["decimal"]))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _atomic_write(path: Path, contents: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.replace(path)


def _crop(frame: Frame, region: tuple[int, int, int, int]) -> Frame:
    x, y, width, height = region
    return frame[y : y + height, x : x + width]


def _mean_conf(tokens: Sequence[Mapping[str, Any]]) -> float:
    if not tokens:
        return -1.0
    return sum(float(token["confidence"]) for token in tokens) / len(tokens)


def _int_at(data: Mapping[str, Sequence[Any]], key: str, index: int) -> int:
    try:
        return int(data.get(key, [])[index])
    except (IndexError, TypeError, ValueError):
        return 0


def _float_at(
    data: Mapping[str, Sequence[Any]],
    key: str,
    index: int,
    default: float,
) -> float:
    try:
        return float(data.get(key, [])[index])
    except (IndexError, TypeError, ValueError):
        return default


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _display_key(key: str) -> str:
    return (key or "").replace("_", " ").title()


def _display_source_method(source_method: str) -> str:
    if source_method == "android_clipboard":
        return "Android clipboard (exact copied text)"
    return "scrolling screenshot OCR"


def _render_observed_value(field: str, value: Any) -> str:
    """Render compact actual-value evidence without implying configured intent."""

    if field == "run_identity" and isinstance(value, Mapping):
        return str(value.get("label") or "unknown")
    if field in {"cards_deck", "workshop_preset", "bots_preset"} and isinstance(
        value, Mapping
    ):
        label = value.get("label")
        slot = value.get("slot")
        return str(label or f"selected slot {slot or 'unknown'}")
    if field == "free_upgrade_locks" and isinstance(value, Mapping):
        locks = value.get("locks")
        if isinstance(locks, Sequence) and not isinstance(locks, (str, bytes)):
            return "; ".join(
                f"{item.get('label', 'unknown')}={item.get('state', 'unknown')}"
                if isinstance(item, Mapping)
                else str(item)
                for item in locks
            )
    if field == "modules" and isinstance(value, Sequence) and not isinstance(
        value, (str, bytes)
    ):
        return "; ".join(
            f"{item.get('slot_key', 'slot')}="
            f"{item.get('name') or item.get('status', 'unknown')}"
            if isinstance(item, Mapping)
            else str(item)
            for item in value
        )
    if field == "damage_slider" and isinstance(value, Mapping):
        return f"{value.get('mode', 'unknown')} {value.get('percentage', 'unknown')}"
    if field == "auto_pick_perks" and isinstance(value, Mapping):
        enabled = value.get("enabled")
        return "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
    if field in {
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    } and isinstance(value, Mapping):
        selected = value.get("selected")
        if isinstance(selected, Sequence) and not isinstance(selected, (str, bytes)):
            labels = [
                str(item.get("display_text") or "unknown")
                if isinstance(item, Mapping)
                else str(item)
                for item in selected
            ]
            return " > ".join(labels) if labels else "none"
    if field == "ultimate_weapons" and isinstance(value, Mapping):
        return "; ".join(
            f"{weapon}: "
            + ", ".join(f"{name}={state}" for name, state in toggles.items())
            if isinstance(toggles, Mapping)
            else f"{weapon}: {toggles}"
            for weapon, toggles in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return " > ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def _display_row_quality(row: Mapping[str, Any]) -> str:
    if row.get("source") == "android_clipboard":
        return "exact clipboard text"
    return f"OCR {float(row.get('confidence', -1.0)):.1f}"


def _display_derived(key: str, value: Any) -> str:
    if key.endswith("_decimal"):
        try:
            return format_tower_number(Decimal(str(value)))
        except InvalidOperation:
            return str(value)
    if key.endswith("_percent"):
        return f"{value}%"
    if key == "effective_game_speed":
        return f"x{value}"
    return str(value)


def _display_derived_key(key: str) -> str:
    labels = {
        "reroll_dice_per_real_hour_decimal": "Reroll Dice/hour",
        "module_shards_per_real_hour_decimal": "Shards/hour (total module shards)",
    }
    if key in labels:
        return labels[key]
    if key.endswith("_decimal"):
        key = key.removesuffix("_decimal")
    return _display_key(key)


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", "", (value or "")).lower()


def _normalize_ocr_value(value: str) -> str:
    text = (value or "").strip()
    if re.fullmatch(r"[0Oo]\)", text):
        return "0"
    suffix_artifact = re.fullmatch(
        r"([+-]?\d+\.([0-9]+))[lI]([a-z]{2})",
        text,
    )
    if suffix_artifact:
        number, fraction, suffix = suffix_artifact.groups()
        # The thin digit 1 is commonly read as l immediately before a two-letter
        # magnitude. If the number already ends in 1, the l is a duplicate.
        return number + ("" if fraction.endswith("1") else "1") + suffix
    return text


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_RECORDS_DIR",
    "SCHEMA_VERSION",
    "attach_battle_perks",
    "attach_observed_run_configuration",
    "build_battle_record",
    "build_battle_record_from_clipboard",
    "compare_battle_identity",
    "derive_battle_stats",
    "format_tower_number",
    "make_battle_id",
    "ocr_game_stats",
    "ocr_more_stats",
    "parse_more_stats_clipboard",
    "parse_duration_seconds",
    "parse_tower_number",
    "persist_battle_record",
    "render_battle_markdown",
    "render_coin_rate_samples_markdown",
    "render_survival_ability_activations_markdown",
]
