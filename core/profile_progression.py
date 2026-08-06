"""Versioned, structural snapshots of save-backed account progression.

The projection deliberately keeps source-field and index identity when a game
semantic has not been validated.  It is suitable for run-to-run comparison,
but it does not grant automation authority or turn an indexed value into a
claimed gameplay formula.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Optional


PROFILE_PROGRESSION_SCHEMA_VERSION = 1
PROFILE_PROGRESSION_DELTA_SCHEMA_VERSION = 1
PROFILE_EVIDENCE_LEVELS = frozenset(
    {"structural", "cross_channel", "causal", "shortcut_ready"}
)


class ProfileProgressionError(ValueError):
    """The exact-version progression specification or value is malformed."""


def normalize_profile_progression(
    decoded: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *,
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a safe structural projection from an exact-version save root."""

    specification = mapping.get("profile_progression")
    if not isinstance(specification, Mapping):
        return unavailable_profile_progression(
            "exact_version_progression_mapping_unavailable",
            captured_at=capture.get("captured_at"),
            mapping_id=mapping.get("mapping_id"),
        )
    if specification.get("schema_version") != PROFILE_PROGRESSION_SCHEMA_VERSION:
        raise ProfileProgressionError(
            "unsupported profile progression mapping schema"
        )
    raw_components = specification.get("components")
    if not isinstance(raw_components, Mapping) or not raw_components:
        raise ProfileProgressionError("profile progression components are missing")
    component_validation = specification.get("component_validation")
    if not isinstance(component_validation, Mapping):
        raise ProfileProgressionError(
            "profile progression component validation is missing"
        )
    component_names = {str(name) for name in raw_components}
    validation_names = {str(name) for name in component_validation}
    if validation_names != component_names:
        missing = sorted(component_names - validation_names)
        unexpected = sorted(validation_names - component_names)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ProfileProgressionError(
            "profile progression component validation coverage changed: "
            + "; ".join(details)
        )

    components: dict[str, Any] = {}
    warnings: list[str] = []
    for component_name, raw_fields in sorted(raw_components.items()):
        if not isinstance(raw_fields, Mapping) or not raw_fields:
            raise ProfileProgressionError(
                f"component {component_name!r} has no field specification"
            )
        values: dict[str, Any] = {}
        summaries: dict[str, Any] = {}
        component_reasons: list[str] = []
        source_fields: list[str] = []
        validation = _normalize_component_validation(
            component_name,
            component_validation.get(component_name),
        )
        for output_name, raw_spec in sorted(raw_fields.items()):
            if not isinstance(raw_spec, Mapping):
                raise ProfileProgressionError(
                    f"field specification {component_name}.{output_name} is invalid"
                )
            source = str(raw_spec.get("source") or "").strip()
            kind = str(raw_spec.get("kind") or "").strip()
            if not source or not kind:
                raise ProfileProgressionError(
                    f"field specification {component_name}.{output_name} is incomplete"
                )
            source_fields.append(source)
            try:
                normalized = _normalize_field(decoded.get(source), kind, raw_spec)
            except ProfileProgressionError as exc:
                component_reasons.append(f"{source}:{exc}")
                continue
            values[str(output_name)] = normalized
            summary = _value_summary(normalized)
            if summary:
                summaries[str(output_name)] = summary

        complete = not component_reasons and len(values) == len(raw_fields)
        component = {
            "status": "structural" if complete else "partial",
            "complete": complete,
            "validation": validation,
            "source_fields": source_fields,
            "values": values,
            "summary": summaries,
            "fingerprint": _fingerprint(values),
        }
        if component_reasons:
            component["reasons"] = component_reasons
            warnings.extend(
                f"{component_name}:{reason}" for reason in component_reasons
            )
        components[str(component_name)] = component

    complete = all(component["complete"] for component in components.values())
    identity = {
        "data_version": _exact_int(decoded.get("dataVersion")),
        "game_version": _exact_int(decoded.get("versionNumber")),
        "save_revision": _exact_int(decoded.get("saveRevision")),
        "mapping_id": str(mapping.get("mapping_id") or "") or None,
        "audit_matrix_id": str(
            specification.get("audit_matrix_id") or ""
        ) or None,
    }
    source = {
        "captured_at": str(capture.get("captured_at") or ""),
        "sha256": str(capture.get("source_sha256") or ""),
    }
    return {
        "schema_version": PROFILE_PROGRESSION_SCHEMA_VERSION,
        "status": "complete" if complete else "partial",
        "complete": complete,
        "identity": identity,
        "source": source,
        "fingerprint": _fingerprint(
            {
                "mapping_id": identity["mapping_id"],
                "components": {
                    name: component["values"]
                    for name, component in components.items()
                },
            }
        ),
        "components": components,
        "warnings": warnings,
    }


def _normalize_component_validation(
    component_name: Any,
    raw_validation: Any,
) -> dict[str, str]:
    if not isinstance(raw_validation, Mapping):
        raise ProfileProgressionError(
            f"component {component_name!r} validation is invalid"
        )
    audit_id = str(raw_validation.get("audit_id") or "").strip()
    evidence_level = str(raw_validation.get("evidence_level") or "").strip()
    provenance = str(raw_validation.get("provenance") or "").strip()
    if not audit_id:
        raise ProfileProgressionError(
            f"component {component_name!r} validation audit_id is missing"
        )
    if evidence_level not in PROFILE_EVIDENCE_LEVELS:
        raise ProfileProgressionError(
            f"component {component_name!r} validation evidence_level is invalid"
        )
    if not provenance:
        raise ProfileProgressionError(
            f"component {component_name!r} validation provenance is missing"
        )
    return {
        "audit_id": audit_id,
        "evidence_level": evidence_level,
        "provenance": provenance,
    }


def unavailable_profile_progression(
    reason: str,
    *,
    captured_at: Any = None,
    mapping_id: Any = None,
) -> dict[str, Any]:
    """Return an explicit nonblocking placeholder for a missing snapshot."""

    when = str(captured_at or "").strip()
    if not when:
        when = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": PROFILE_PROGRESSION_SCHEMA_VERSION,
        "status": "unavailable",
        "complete": False,
        "reason": str(reason or "profile_progression_unavailable"),
        "identity": {
            "data_version": None,
            "game_version": None,
            "save_revision": None,
            "mapping_id": str(mapping_id or "") or None,
            "audit_matrix_id": None,
        },
        "source": {"captured_at": when, "sha256": ""},
        "fingerprint": "",
        "components": {},
        "warnings": [],
    }


def diff_profile_progression(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Return exact source-path changes between two normalized snapshots."""

    before_identity = _snapshot_identity(before)
    after_identity = _snapshot_identity(after)
    result: dict[str, Any] = {
        "schema_version": PROFILE_PROGRESSION_DELTA_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": "",
        "before": before_identity,
        "after": after_identity,
        "changed_components": [],
        "change_count": 0,
        "changes": [],
    }
    if before.get("status") == "unavailable":
        result["reason"] = "before_snapshot_unavailable"
        return result
    if after.get("status") == "unavailable":
        result["reason"] = "after_snapshot_unavailable"
        return result
    if before.get("complete") is not True or after.get("complete") is not True:
        result["reason"] = "incomplete_snapshot"
        return result
    if (
        before.get("schema_version") != PROFILE_PROGRESSION_SCHEMA_VERSION
        or after.get("schema_version") != PROFILE_PROGRESSION_SCHEMA_VERSION
    ):
        result["status"] = "incompatible"
        result["reason"] = "snapshot_schema_mismatch"
        return result
    before_mapping = before_identity.get("mapping_id")
    after_mapping = after_identity.get("mapping_id")
    if not before_mapping or before_mapping != after_mapping:
        result["status"] = "incompatible"
        result["reason"] = "mapping_id_mismatch"
        return result

    before_components = before.get("components")
    after_components = after.get("components")
    if not isinstance(before_components, Mapping) or not isinstance(
        after_components, Mapping
    ):
        result["reason"] = "component_projection_unavailable"
        return result

    changes: list[dict[str, Any]] = []
    changed_components: list[str] = []
    for component_name in sorted(set(before_components) | set(after_components)):
        before_component = before_components.get(component_name)
        after_component = after_components.get(component_name)
        before_values = (
            before_component.get("values")
            if isinstance(before_component, Mapping)
            else None
        )
        after_values = (
            after_component.get("values")
            if isinstance(after_component, Mapping)
            else None
        )
        component_changes: list[dict[str, Any]] = []
        _collect_changes(
            before_values,
            after_values,
            path=str(component_name),
            destination=component_changes,
        )
        if component_changes:
            changed_components.append(str(component_name))
            changes.extend(component_changes)

    result.update(
        {
            "status": "changed" if changes else "unchanged",
            "reason": "",
            "changed_components": changed_components,
            "change_count": len(changes),
            "changes": changes,
        }
    )
    return result


def baseline_profile_progression_delta(
    snapshot: Mapping[str, Any],
    *,
    reason: str = "prior_profile_progression_snapshot_unavailable",
) -> dict[str, Any]:
    """Describe a captured snapshot that has no earlier comparison baseline."""

    return {
        "schema_version": PROFILE_PROGRESSION_DELTA_SCHEMA_VERSION,
        "status": "baseline",
        "reason": reason,
        "before": None,
        "after": _snapshot_identity(snapshot),
        "changed_components": [],
        "change_count": 0,
        "changes": [],
    }


def unavailable_profile_progression_delta(
    snapshot: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Describe a run whose current profile snapshot cannot be compared."""

    return {
        "schema_version": PROFILE_PROGRESSION_DELTA_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": str(reason or "profile_progression_delta_unavailable"),
        "before": None,
        "after": _snapshot_identity(snapshot),
        "changed_components": [],
        "change_count": 0,
        "changes": [],
    }


def render_profile_progression_markdown(
    snapshot: Any,
    delta: Any = None,
) -> list[str]:
    """Render a compact structural summary and exact run-to-run changes."""

    if not isinstance(snapshot, Mapping):
        return []
    lines = ["", "## Profile Progression", ""]
    status = str(snapshot.get("status") or "unavailable")
    identity = snapshot.get("identity") or {}
    source = snapshot.get("source") or {}
    if status == "unavailable":
        lines.append(
            "Snapshot unavailable: "
            + str(snapshot.get("reason") or "unspecified reason")
        )
        return lines
    lines.append(
        "Snapshot: "
        f"{status}; mapping `{identity.get('mapping_id') or 'unknown'}`; "
        f"save revision {identity.get('save_revision', 'unknown')}; "
        f"captured {source.get('captured_at') or 'unknown'}"
    )
    fingerprint = str(snapshot.get("fingerprint") or "")
    if fingerprint:
        lines.append(f"Fingerprint: `{fingerprint[:16]}`")

    components = snapshot.get("components") or {}
    themes = components.get("themes") if isinstance(components, Mapping) else None
    if isinstance(themes, Mapping):
        summary = themes.get("summary") or {}
        theme_parts = []
        for key, label in (
            ("tower_unlocked", "Tower"),
            ("background_unlocked", "Backgrounds"),
            ("menu_unlocked", "Menu"),
        ):
            counts = summary.get(key) if isinstance(summary, Mapping) else None
            if isinstance(counts, Mapping) and "true_count" in counts:
                theme_parts.append(
                    f"{label} {counts['true_count']}/{counts.get('length', '?')}"
                )
        if theme_parts:
            lines.append("- Themes: " + ", ".join(theme_parts))
    relics = components.get("relics") if isinstance(components, Mapping) else None
    if isinstance(relics, Mapping):
        summary = relics.get("summary") or {}
        counts = summary.get("unlocked") if isinstance(summary, Mapping) else None
        if isinstance(counts, Mapping) and "nonzero_count" in counts:
            lines.append(
                "- Relics: "
                f"{counts['nonzero_count']}/{counts.get('length', '?')} unlocked"
            )
    if isinstance(components, Mapping):
        partial = sorted(
            name
            for name, component in components.items()
            if isinstance(component, Mapping) and not component.get("complete")
        )
        lines.append(f"- Components recorded: {len(components)}")
        if partial:
            lines.append("- Partial components: " + ", ".join(partial))

    if not isinstance(delta, Mapping):
        return lines
    lines.extend(["", "### Changes from prior recorded run", ""])
    baseline = delta.get("baseline_record")
    if isinstance(baseline, Mapping) and baseline.get("record_id"):
        lines.append(f"Baseline: `{baseline['record_id']}`")
    delta_status = str(delta.get("status") or "unavailable")
    if delta_status == "unchanged":
        lines.append("No save-backed profile progression changes were detected.")
        return lines
    if delta_status == "baseline":
        lines.append("No earlier compatible progression snapshot is recorded.")
        return lines
    if delta_status not in {"changed"}:
        lines.append(
            "Comparison unavailable: "
            + str(delta.get("reason") or delta_status)
        )
        return lines
    changes = [
        change
        for change in delta.get("changes") or []
        if isinstance(change, Mapping)
    ]
    for change in changes[:50]:
        lines.append(
            f"- `{change.get('path')}`: "
            f"{_markdown_value(change.get('before'))} → "
            f"{_markdown_value(change.get('after'))}"
        )
    if len(changes) > 50:
        lines.append(f"- …and {len(changes) - 50} more changes in JSON.")
    return lines


def _normalize_field(value: Any, kind: str, spec: Mapping[str, Any]) -> Any:
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ProfileProgressionError("expected_boolean")
        return value
    if kind == "integer":
        exact = _exact_int(value)
        if exact is None:
            raise ProfileProgressionError("expected_integer")
        return exact
    if kind in {"boolean_vector", "integer_vector", "string_vector"}:
        return _normalize_primitive_vector(value, kind, spec)
    if kind == "bot_presets":
        return _normalize_bot_presets(value, spec)
    if kind == "module_items":
        return _normalize_module_items(value, spec)
    if kind == "assist_module_slots":
        return _normalize_assist_slots(value, spec)
    raise ProfileProgressionError(f"unsupported_kind:{kind}")


def _normalize_primitive_vector(
    value: Any,
    kind: str,
    spec: Mapping[str, Any],
) -> list[Any]:
    sequence = _required_sequence(value)
    _require_length(sequence, spec)
    result: list[Any] = []
    for item in sequence:
        if kind == "boolean_vector" and isinstance(item, bool):
            result.append(item)
        elif kind == "integer_vector" and _exact_int(item) is not None:
            result.append(item)
        elif kind == "string_vector" and isinstance(item, str):
            result.append(item)
        else:
            raise ProfileProgressionError(f"invalid_{kind}_element")
    return result


def _normalize_bot_presets(value: Any, spec: Mapping[str, Any]) -> list[Any]:
    sequence = _required_sequence(value)
    _require_length(sequence, spec)
    result = []
    for preset in sequence:
        if not isinstance(preset, Mapping):
            raise ProfileProgressionError("invalid_bot_preset")
        levels = _normalize_fixed_integer_vector(preset.get("levels"), 4)
        selected = _normalize_fixed_integer_vector(
            preset.get("selectedLevels"), 4
        )
        unlocked = preset.get("unlocked")
        active = preset.get("active")
        plus_unlocked = preset.get("plusUnlocked")
        plus_level = _exact_int(preset.get("plusLevel"))
        if not all(isinstance(item, bool) for item in (unlocked, active, plus_unlocked)):
            raise ProfileProgressionError("invalid_bot_preset_boolean")
        if plus_level is None:
            raise ProfileProgressionError("invalid_bot_plus_level")
        result.append(
            {
                "unlocked": unlocked,
                "active": active,
                "levels": levels,
                "selected_levels": selected,
                "plus_unlocked": plus_unlocked,
                "plus_level": plus_level,
            }
        )
    return result


def _normalize_module_items(value: Any, spec: Mapping[str, Any]) -> list[Any]:
    sequence = _required_sequence(value)
    _require_length(sequence, spec)
    return [_normalize_module_item(item) for item in sequence]


def _normalize_module_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileProgressionError("invalid_module_item")
    info_index = _exact_int(value.get("infoIndex"))
    rarity = _exact_int(value.get("currentRarity"))
    level = _exact_int(value.get("level"))
    if None in (info_index, rarity, level):
        raise ProfileProgressionError("invalid_module_identity_or_level")
    effects = _normalize_fixed_integer_vector(value.get("effects"), 8)
    locked = _normalize_fixed_boolean_vector(value.get("effectLocked"), 8)
    return {
        "info_index": info_index,
        "rarity": rarity,
        "level": level,
        "effects": effects,
        "effect_locked": locked,
    }


def _normalize_assist_slots(value: Any, spec: Mapping[str, Any]) -> list[Any]:
    sequence = _required_sequence(value)
    _require_length(sequence, spec)
    result = []
    for slot in sequence:
        if not isinstance(slot, Mapping):
            raise ProfileProgressionError("invalid_assist_module_slot")
        unlocked = slot.get("unlocked")
        slot_type = _exact_int(slot.get("type"))
        if not isinstance(unlocked, bool) or slot_type is None:
            raise ProfileProgressionError("invalid_assist_slot_identity")
        module = slot.get("equippedModule")
        if module is None:
            module = slot.get("module")
        result.append(
            {
                "unlocked": unlocked,
                "type": slot_type,
                "module": _normalize_module_item(module),
                "unique_effect_efficiency_level": _required_int(
                    slot.get("uniqueEffectEfficiencyLevel"),
                    "invalid_unique_effect_efficiency_level",
                ),
                "main_effect_efficiency_level": _required_int(
                    slot.get("mainEffectEfficiencyLevel"),
                    "invalid_main_effect_efficiency_level",
                ),
                "substat_efficiency_level": _required_int(
                    slot.get("substatEfficiencyLevel"),
                    "invalid_substat_efficiency_level",
                ),
            }
        )
    return result


def _required_sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ProfileProgressionError("expected_sequence")
    return list(value)


def _require_length(value: Sequence[Any], spec: Mapping[str, Any]) -> None:
    expected = _exact_int(spec.get("length"))
    if expected is not None and len(value) != expected:
        raise ProfileProgressionError(
            f"length_changed:expected={expected}:actual={len(value)}"
        )


def _normalize_fixed_integer_vector(value: Any, length: int) -> list[int]:
    sequence = _required_sequence(value)
    if len(sequence) != length or any(_exact_int(item) is None for item in sequence):
        raise ProfileProgressionError("invalid_integer_vector")
    return list(sequence)


def _normalize_fixed_boolean_vector(value: Any, length: int) -> list[bool]:
    sequence = _required_sequence(value)
    if len(sequence) != length or any(not isinstance(item, bool) for item in sequence):
        raise ProfileProgressionError("invalid_boolean_vector")
    return list(sequence)


def _required_int(value: Any, reason: str) -> int:
    result = _exact_int(value)
    if result is None:
        raise ProfileProgressionError(reason)
    return result


def _value_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    summary: dict[str, Any] = {"length": len(value)}
    if all(isinstance(item, bool) for item in value):
        summary["true_count"] = sum(value)
    elif all(_exact_int(item) is not None for item in value):
        summary["nonzero_count"] = sum(item != 0 for item in value)
    return summary


def _snapshot_identity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    identity = snapshot.get("identity")
    source = snapshot.get("source")
    return {
        "fingerprint": str(snapshot.get("fingerprint") or ""),
        "mapping_id": (
            identity.get("mapping_id") if isinstance(identity, Mapping) else None
        ),
        "save_revision": (
            identity.get("save_revision") if isinstance(identity, Mapping) else None
        ),
        "captured_at": (
            source.get("captured_at") if isinstance(source, Mapping) else None
        ),
    }


def _collect_changes(
    before: Any,
    after: Any,
    *,
    path: str,
    destination: list[dict[str, Any]],
) -> None:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            _collect_changes(
                before.get(key),
                after.get(key),
                path=f"{path}.{key}",
                destination=destination,
            )
        return
    if _is_sequence(before) and _is_sequence(after):
        before_values = list(before)
        after_values = list(after)
        if len(before_values) != len(after_values):
            destination.append(
                {
                    "path": path,
                    "before": {"length": len(before_values)},
                    "after": {"length": len(after_values)},
                }
            )
            return
        for index, (before_value, after_value) in enumerate(
            zip(before_values, after_values)
        ):
            _collect_changes(
                before_value,
                after_value,
                path=f"{path}[{index}]",
                destination=destination,
            )
        return
    if before != after:
        destination.append({"path": path, "before": before, "after": after})


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _markdown_value(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return f"`{value}`"


def _exact_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


__all__ = [
    "PROFILE_PROGRESSION_DELTA_SCHEMA_VERSION",
    "PROFILE_PROGRESSION_SCHEMA_VERSION",
    "ProfileProgressionError",
    "baseline_profile_progression_delta",
    "diff_profile_progression",
    "normalize_profile_progression",
    "render_profile_progression_markdown",
    "unavailable_profile_progression",
    "unavailable_profile_progression_delta",
]
