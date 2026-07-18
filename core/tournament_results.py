"""Structured capture and persistence for completed Tournament runs."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.battle_stats import parse_more_stats_clipboard, parse_tower_number
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
SUMMARY_CROP = (100, 300, 880, 1380)
DEFAULT_RECORDS_DIR = Path("logs/tournaments")
SCHEMA_VERSION = 1
_REQUIRED_SUMMARY_FIELDS = {
    "league",
    "wave",
    "killed_by",
    "rank",
    "coins_earned",
    "ad_coins_earned",
}


def make_tournament_id(captured_at: Optional[datetime] = None) -> str:
    """Return a second-resolution, timezone-aware Tournament identifier."""

    when = captured_at or datetime.now().astimezone()
    return "Tournament" + when.strftime("%Y%m%dT%H%M%S%z")


def ocr_tournament_summary(
    frame: Frame,
    *,
    text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """Read the Tournament Stats summary without dismissing it."""

    x, y, w, h = SUMMARY_CROP
    raw_text, confidence = text_fn(frame[y : y + h, x : x + w], psm=6)
    normalized = " ".join((raw_text or "").split())
    fields: dict[str, Any] = {}

    patterns: tuple[tuple[str, str, Callable[[str], Any]], ...] = (
        (
            "league",
            r"TOURNAMENT\s+STATS\s+(.+?\s+League)(?=\s+Wave\s+\d)",
            str,
        ),
        ("wave", r"\bWave\s+(\d{1,7})\b", int),
        (
            "killed_by",
            r"\bKilled\s+By\s+(.+?)(?=\s+currently\s+at\s+rank)",
            str,
        ),
        ("rank", r"currently\s+at\s+rank\s*:\s*(\d+)", int),
    )
    for key, pattern, converter in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            fields[key] = {
                "raw": value,
                "value": converter(value),
                "confidence": round(float(confidence), 1),
            }

    coins_match = re.search(
        r"coins\s+earned\s+ad\s+coins\s+earned\s+"
        r"([0-9.]+\s*[KMBTqQsSOND]|[0-9.]+\s*[a-z]{2}|[0-9.]+)\s*\S*\s*"
        r"([0-9.]+\s*[KMBTqQsSOND]|[0-9.]+\s*[a-z]{2}|[0-9.]+)",
        normalized,
        re.IGNORECASE,
    )
    if coins_match:
        for key, raw in zip(
            ("coins_earned", "ad_coins_earned"),
            coins_match.groups(),
        ):
            compact = raw.replace(" ", "")
            value = parse_tower_number(compact)
            fields[key] = {
                "raw": compact,
                "decimal": str(value) if value is not None else None,
                "confidence": round(float(confidence), 1),
            }

    missing = sorted(_REQUIRED_SUMMARY_FIELDS - set(fields))
    return {
        "raw_text": normalized,
        "confidence": round(float(confidence), 1),
        "fields": fields,
        "quality": {
            "valid": not missing,
            "missing_required_fields": missing,
        },
    }


def build_tournament_result(
    summary_frame: Frame,
    clipboard_text: Optional[str] = None,
    *,
    detailed_reason: str = "not_captured",
    tournament_id: Optional[str] = None,
    captured_at: Optional[datetime] = None,
    strategy_name: Optional[str] = None,
    run_configuration: Optional[Mapping[str, Any]] = None,
    runtime_context: Optional[Mapping[str, Any]] = None,
    summary_text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """Build one Tournament result from its summary and optional copied report."""

    when = captured_at or datetime.now().astimezone()
    summary = ocr_tournament_summary(summary_frame, text_fn=summary_text_fn)
    if clipboard_text:
        detailed = parse_more_stats_clipboard(clipboard_text)
    else:
        detailed = {
            "source_method": "unavailable",
            "page_count": 0,
            "raw_text": "",
            "sections": [],
            "quality": {
                "valid": False,
                "source_complete": False,
                "source_reason": detailed_reason,
                "source_method": "unavailable",
                "warnings": [f"Detailed Tournament stats unavailable: {detailed_reason}"],
                "retain_source_images": True,
            },
        }

    identity = _compare_wave(summary, detailed)
    warnings = list(detailed["quality"].get("warnings", []))
    if not summary["quality"]["valid"]:
        warnings.append(
            "Missing required Tournament summary fields: "
            + ", ".join(summary["quality"]["missing_required_fields"])
        )
    if identity["mismatch"]:
        warnings.append("Tournament summary/detailed wave mismatch")
    valid = bool(
        summary["quality"]["valid"]
        and detailed["quality"]["valid"]
        and not identity["mismatch"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "tournament_id": tournament_id or make_tournament_id(when),
        "captured_at": when.isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "run_configuration": copy.deepcopy(dict(run_configuration or {})),
        "runtime": dict(runtime_context or {}),
        "summary": summary,
        "detailed_stats": detailed,
        "quality": {
            "valid": valid,
            "retain_source_images": not valid,
            "warnings": warnings,
            "identity": identity,
        },
    }


def _compare_wave(
    summary: Mapping[str, Any],
    detailed: Mapping[str, Any],
) -> dict[str, Any]:
    summary_wave = (
        summary.get("fields", {}).get("wave", {}).get("value")
    )
    detailed_wave = None
    for section in detailed.get("sections", []):
        if section.get("key") != "battle_report":
            continue
        for row in section.get("rows", []):
            if row.get("key") == "wave":
                detailed_wave = row.get("value")
                break
    return {
        "summary_wave": summary_wave,
        "detailed_wave": detailed_wave,
        "checked": summary_wave is not None and detailed_wave is not None,
        "mismatch": bool(
            summary_wave is not None
            and detailed_wave is not None
            and int(summary_wave) != int(detailed_wave)
        ),
    }


def persist_tournament_result(
    record: Mapping[str, Any],
    *,
    records_dir: Path | str = DEFAULT_RECORDS_DIR,
) -> tuple[Path, Path]:
    """Atomically persist JSON plus a concise Markdown Tournament view."""

    directory = Path(records_dir)
    directory.mkdir(parents=True, exist_ok=True)
    result_id = str(record["tournament_id"])
    json_path = directory / f"{result_id}.json"
    markdown_path = directory / f"{result_id}.md"
    _atomic_write(
        json_path,
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write(markdown_path, render_tournament_markdown(record))
    return json_path, markdown_path


def find_recent_tournament_result(
    summary_frame: Frame,
    *,
    records_dir: Path | str = DEFAULT_RECORDS_DIR,
    now: Optional[datetime] = None,
    within_seconds: float = 12 * 60 * 60,
) -> Optional[dict[str, Any]]:
    """Return a recent persisted result matching every summary identity field."""

    current = ocr_tournament_summary(summary_frame)
    if not current["quality"]["valid"]:
        return None
    expected = _summary_identity(current)
    current_time = now or datetime.now().astimezone()
    directory = Path(records_dir)
    if not directory.exists():
        return None
    paths = sorted(
        directory.glob("Tournament*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            captured_at = datetime.fromisoformat(str(record["captured_at"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if not record.get("quality", {}).get("valid"):
            continue
        age = (current_time - captured_at).total_seconds()
        if age < 0 or age > float(within_seconds):
            continue
        if _summary_identity(record.get("summary", {})) == expected:
            return record
    return None


def _summary_identity(summary: Mapping[str, Any]) -> tuple[Any, ...]:
    fields = summary.get("fields", {})
    values = []
    for key in (
        "league",
        "wave",
        "killed_by",
        "rank",
        "coins_earned",
        "ad_coins_earned",
    ):
        field = fields.get(key, {})
        values.append(field.get("raw", field.get("value")))
    return tuple(values)


def render_tournament_markdown(record: Mapping[str, Any]) -> str:
    """Render the Tournament summary and every copied Round Stats row."""

    lines = [f"# {record.get('tournament_id', 'Tournament')}", ""]
    lines.append(f"Captured: {record.get('captured_at', 'unknown')}")
    if record.get("strategy"):
        lines.append(f"Strategy: {record['strategy']}")
    fields = record.get("summary", {}).get("fields", {})
    lines.extend(["", "## Result", ""])
    for key, label in (
        ("league", "League"),
        ("wave", "Wave"),
        ("rank", "Rank at completion"),
        ("killed_by", "Killed by"),
        ("coins_earned", "Coins earned"),
        ("ad_coins_earned", "Ad coins earned"),
    ):
        field = fields.get(key)
        if not field:
            continue
        value = field.get("raw", field.get("value", ""))
        lines.append(f"- {label}: {value}")

    sections = record.get("detailed_stats", {}).get("sections", [])
    for section in sections:
        lines.extend(["", f"## {section.get('name', 'Stats')}", ""])
        lines.extend(["| Stat | Value |", "| --- | ---: |"])
        for row in section.get("rows", []):
            lines.append(
                f"| {row.get('label', '')} | {row.get('value_raw', '')} |"
            )

    warnings = record.get("quality", {}).get("warnings", [])
    lines.extend(["", "## Quality", ""])
    lines.append(
        "- Valid: " + ("yes" if record.get("quality", {}).get("valid") else "no")
    )
    for warning in warnings:
        lines.append(f"- Warning: {warning}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


__all__ = [
    "build_tournament_result",
    "find_recent_tournament_result",
    "make_tournament_id",
    "ocr_tournament_summary",
    "persist_tournament_result",
    "render_tournament_markdown",
]
