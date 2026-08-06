"""Structured capture and persistence for completed Tournament runs."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional

import numpy as np

from core.battle_classification import (
    analyze_battle_type,
    observed_tier_for_record,
    unbound_run_evidence_warning,
)
from core.battle_stats import (
    parse_more_stats_clipboard,
    parse_tower_number,
    render_coin_rate_samples_markdown,
    render_perk_selection_timeline_markdown,
    render_survival_ability_activations_markdown,
)
from core.tournament_conditions import (
    derive_tournament_conditions,
    tournament_conditions_complete,
    unavailable_tournament_conditions,
)
from core.profile_progression import render_profile_progression_markdown
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
SUMMARY_CROP = (100, 300, 880, 1380)
DEFAULT_RECORDS_DIR = Path("logs/tournaments")
SCHEMA_VERSION = 4
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
    detailed_stats: Optional[Mapping[str, Any]] = None,
    detailed_reason: str = "not_captured",
    tournament_id: Optional[str] = None,
    captured_at: Optional[datetime] = None,
    strategy_name: Optional[str] = None,
    run_configuration: Optional[Mapping[str, Any]] = None,
    runtime_context: Optional[Mapping[str, Any]] = None,
    battle_conditions: Optional[Mapping[str, Any]] = None,
    summary_text_fn: Callable[..., tuple[str, float]] = ocr_text_and_conf,
) -> dict[str, Any]:
    """Build one Tournament result from its summary and optional copied report."""

    when = captured_at or datetime.now().astimezone()
    summary = ocr_tournament_summary(summary_frame, text_fn=summary_text_fn)
    if detailed_stats is not None:
        detailed = copy.deepcopy(dict(detailed_stats))
    elif clipboard_text:
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
    runtime = dict(runtime_context or {})
    profile_progression = runtime.pop("profile_progression", None)
    run_binding_warning = unbound_run_evidence_warning(runtime)
    if run_binding_warning is not None:
        warnings.append(run_binding_warning)
    observed_tier = observed_tier_for_record(
        {"runtime": runtime, "detailed_stats": detailed}
    )
    if observed_tier is not None:
        runtime["observed_tier"] = observed_tier
    classification = analyze_battle_type(
        strategy_name=strategy_name,
        run_configuration=run_configuration,
        terminal_state=runtime.get("terminal_state") or "TOURNAMENT_RESULTS",
        record_id=tournament_id,
        observed_tier=observed_tier,
    )
    conditions = _conditions_for_summary(battle_conditions, summary)
    record = {
        "schema_version": SCHEMA_VERSION,
        "tournament_id": tournament_id or make_tournament_id(when),
        "captured_at": when.isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "battle_type": classification["type"],
        "battle_type_analysis": classification,
        "run_configuration": copy.deepcopy(dict(run_configuration or {})),
        "runtime": runtime,
        "battle_conditions": conditions,
        "summary": summary,
        "detailed_stats": detailed,
        "quality": {
            "valid": valid,
            "retain_source_images": not valid,
            "warnings": warnings,
            "identity": identity,
        },
    }
    if isinstance(profile_progression, Mapping):
        record["profile_progression"] = copy.deepcopy(
            dict(profile_progression)
        )
    return record


def attach_tournament_conditions(
    record: Mapping[str, Any],
    evidence: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a record copy with normalized nonblocking condition evidence."""

    result = copy.deepcopy(dict(record))
    result["schema_version"] = max(
        SCHEMA_VERSION,
        int(result.get("schema_version") or 0),
    )
    result["battle_conditions"] = _conditions_for_summary(
        evidence,
        result.get("summary", {}),
    )
    return result


def _conditions_for_summary(
    evidence: Optional[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        return unavailable_tournament_conditions("not_captured")
    candidate = copy.deepcopy(dict(evidence))
    if not tournament_conditions_complete(candidate):
        return candidate

    summary_league = str(
        summary.get("fields", {}).get("league", {}).get("value") or ""
    ).strip()
    evidence_league = str(candidate.get("league", {}).get("name") or "").strip()
    if (
        summary_league
        and evidence_league
        and summary_league.casefold() != evidence_league.casefold()
    ):
        return unavailable_tournament_conditions(
            "summary_league_mismatch",
            data_version=candidate.get("data_version"),
            game_version=candidate.get("game_version"),
            tournament_number=candidate.get("tournament_number"),
            league_id=candidate.get("league", {}).get("id"),
            source=candidate.get("source"),
        )
    return candidate


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


def backfill_tournament_conditions(
    event_numbers_by_utc_date: Mapping[str, int],
    *,
    records_dir: Path | str = DEFAULT_RECORDS_DIR,
    data_version: int = 9,
    game_version: int = 1073,
    league_id: int = 5,
    write: bool = False,
    attached_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Plan or apply an explicit-date condition backfill to Tournament records.

    Dates are supplied by the caller rather than inferred indefinitely from a
    calendar cadence. Existing complete evidence is never replaced when its
    Tournament number differs from the requested event.
    """

    directory = Path(records_dir)
    when = (attached_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    items: list[dict[str, Any]] = []
    if not directory.exists():
        return {
            "schema_version": 1,
            "write": bool(write),
            "records_dir": str(directory),
            "items": [],
            "summary": {"total": 0, "updated": 0, "planned": 0, "skipped": 0},
        }

    for path in sorted(directory.glob("Tournament*.json")):
        item: dict[str, Any] = {"file": path.name}
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, Mapping):
                raise ValueError("record root is not an object")
            if str(record.get("tournament_id") or "") != path.stem:
                raise ValueError("Tournament id does not match filename")
            captured = datetime.fromisoformat(str(record["captured_at"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            item.update({"action": "skipped", "reason": f"unreadable:{exc}"})
            items.append(item)
            continue

        event_date = captured.astimezone(timezone.utc).date().isoformat()
        item["event_date"] = event_date
        tournament_number = event_numbers_by_utc_date.get(event_date)
        if tournament_number is None:
            item.update({"action": "skipped", "reason": "event_date_unmapped"})
            items.append(item)
            continue

        existing = record.get("battle_conditions")
        if tournament_conditions_complete(existing):
            existing_number = existing.get("tournament_number")
            if existing_number != tournament_number:
                item.update(
                    {
                        "action": "skipped",
                        "reason": "existing_tournament_number_conflict",
                        "tournament_number": tournament_number,
                        "existing_tournament_number": existing_number,
                    }
                )
            else:
                item.update(
                    {
                        "action": "unchanged",
                        "reason": "complete_evidence_already_present",
                        "tournament_number": tournament_number,
                        "codes": list(existing.get("summary_codes") or []),
                    }
                )
            items.append(item)
            continue

        evidence = derive_tournament_conditions(
            tournament_number,
            league_id,
            data_version=data_version,
            game_version=game_version,
            source={
                "kind": "historical_calibration",
                "method": "explicit_utc_event_date_mapping",
                "event_date": event_date,
                "attached_at": when.isoformat(),
            },
        )
        updated = attach_tournament_conditions(record, evidence)
        normalized = updated["battle_conditions"]
        if not tournament_conditions_complete(normalized):
            item.update(
                {
                    "action": "skipped",
                    "reason": str(normalized.get("reason") or "evidence_incomplete"),
                    "tournament_number": tournament_number,
                }
            )
            items.append(item)
            continue
        if write:
            persist_tournament_result(updated, records_dir=directory)
        item.update(
            {
                "action": "updated" if write else "planned",
                "reason": "",
                "tournament_number": tournament_number,
                "codes": list(normalized.get("summary_codes") or []),
            }
        )
        items.append(item)

    return {
        "schema_version": 1,
        "write": bool(write),
        "records_dir": str(directory),
        "items": items,
        "summary": {
            "total": len(items),
            "updated": sum(item.get("action") == "updated" for item in items),
            "planned": sum(item.get("action") == "planned" for item in items),
            "unchanged": sum(item.get("action") == "unchanged" for item in items),
            "skipped": sum(item.get("action") == "skipped" for item in items),
        },
    }


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
    if record.get("battle_type"):
        lines.append(f"Battle type: {str(record['battle_type']).title()}")
    observed_tier = observed_tier_for_record(record)
    if observed_tier is not None:
        lines.append(f"Observed tier: {observed_tier}")
    fields = record.get("summary", {}).get("fields", {})
    detailed_source = record.get("detailed_stats", {}).get("source_method")
    if detailed_source == "player_save_battle_history":
        lines.append("Stats source: Player save Battle History")
    elif detailed_source == "android_clipboard":
        lines.append("Stats source: Android clipboard")
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

    conditions = record.get("battle_conditions", {})
    lines.extend(["", "## Battle conditions", ""])
    if tournament_conditions_complete(conditions):
        lines.append(
            f"- Tournament number: {conditions.get('tournament_number')}"
        )
        lines.append(
            "- Codes: " + " / ".join(conditions.get("summary_codes", []))
        )
        source = conditions.get("source", {})
        source_kind = source.get("kind") or "unknown"
        source_method = source.get("method") or "unknown"
        lines.append(f"- Provenance: {source_kind} ({source_method})")
        lines.append(
            "- UI fallback preserved: "
            + (
                "yes"
                if conditions.get("ui_fallback", {}).get("preserved")
                else "no"
            )
        )
        lines.extend(
            [
                "",
                "| Category | Code | Condition | Selection |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in conditions.get("heat", []) + conditions.get("overheat", []):
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(item.get("category") or ""),
                        str(item.get("code") or "—"),
                        str(item.get("name") or item.get("id") or ""),
                        str(item.get("selection") or ""),
                    )
                )
                + " |"
            )
    else:
        lines.append(f"- Status: {conditions.get('status', 'unavailable')}")
        lines.append(f"- Reason: {conditions.get('reason', 'not_captured')}")
        lines.append("- UI fallback required: yes")

    lines.extend(
        render_profile_progression_markdown(
            record.get("profile_progression")
        )
    )

    sections = record.get("detailed_stats", {}).get("sections", [])
    for section in sections:
        lines.extend(["", f"## {section.get('name', 'Stats')}", ""])
        lines.extend(["| Stat | Value |", "| --- | ---: |"])
        for row in section.get("rows", []):
            lines.append(
                f"| {row.get('label', '')} | {row.get('value_raw', '')} |"
            )

    lines.extend(
        render_coin_rate_samples_markdown(
            record.get("runtime", {}).get("coin_rate_samples", [])
        )
    )
    lines.extend(
        render_survival_ability_activations_markdown(
            record.get("runtime", {}).get(
                "survival_ability_activations",
                {},
            )
        )
    )
    lines.extend(
        render_perk_selection_timeline_markdown(
            record.get("runtime", {}).get("perk_selection_timeline", {})
        )
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
    "attach_tournament_conditions",
    "backfill_tournament_conditions",
    "build_tournament_result",
    "find_recent_tournament_result",
    "make_tournament_id",
    "ocr_tournament_summary",
    "persist_tournament_result",
    "render_tournament_markdown",
]
