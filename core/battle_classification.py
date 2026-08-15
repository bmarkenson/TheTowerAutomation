"""Evidence-based classification for completed Battle, Tournament, and Milestone runs."""

from __future__ import annotations

from typing import Any, Mapping, Optional


KNOWN_BATTLE_TYPES = {
    "farm",
    "tournament",
    "milestone",
    "dissonance",
    "unknown",
}

UNBOUND_RUN_EVIDENCE_WARNING = (
    "Process-local run evidence was omitted because the terminal screen was "
    "not bound to a forced-save battle identity observed on this process and target"
)

_DISSONANCE_PRESET_PREFIXES = {
    "attack": ("attack disso", "atk disso"),
    "utility": ("utility disso", "util disso"),
}


def dissonance_subtype_from_preset_label(label: object) -> Optional[str]:
    """Return a subtype encoded by an explicitly named Dissonance preset."""

    normalized = " ".join(str(label or "").casefold().replace("-", " ").split())
    for subtype, prefixes in _DISSONANCE_PRESET_PREFIXES.items():
        if normalized.startswith(prefixes):
            return subtype.title()
    return None


def unbound_run_evidence_warning(
    runtime_context: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Return the operator warning for an explicitly unbound terminal context."""

    if not isinstance(runtime_context, Mapping):
        return None
    binding = runtime_context.get("run_binding")
    if isinstance(binding, Mapping) and binding.get("status") == "unbound":
        return UNBOUND_RUN_EVIDENCE_WARNING
    return None


def analyze_battle_type(
    *,
    strategy_name: Optional[str],
    run_configuration: Optional[Mapping[str, Any]],
    terminal_state: Optional[str] = None,
    record_id: Optional[str] = None,
    observed_tier: object = None,
    observed_run_configuration: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Classify a completed run without treating shared settings as identity.

    Tournament and Milestone runs intentionally share the Tournament loadout.
    Their terminal screens are distinct, so the terminal state is the deciding
    signal between those two types. Farm strategy/profile identity is the
    strongest Farm signal.
    """

    configuration = run_configuration or {}
    profile = str(configuration.get("profile") or "").strip().lower()
    family = str(configuration.get("family") or "").strip().lower()
    strategy = str(strategy_name or "").strip().lower()
    terminal = str(terminal_state or "").strip().upper()
    identifier = str(record_id or "")
    tier = _normalize_observed_tier(observed_tier)
    observed_identity = _observed_run_identity(observed_run_configuration)
    observed_identity_label = str(observed_identity.get("label") or "").strip()
    observed_identity_family = str(
        observed_identity.get("family") or ""
    ).strip().lower()
    observed_identity_subtype = str(
        observed_identity.get("subtype") or ""
    ).strip().lower()
    observed_identity_signals = observed_identity.get("signals")
    post_run_preset_identity = bool(
        isinstance(observed_identity_signals, Mapping)
        and "post_run_workshop_preset" in observed_identity_signals
    )

    signals: list[str] = []
    if terminal:
        signals.append(f"terminal_state:{terminal}")
    elif identifier.startswith("Tournament"):
        terminal = "TOURNAMENT_RESULTS"
        signals.append("record_identity:tournament_result")
    elif identifier.startswith("Battle"):
        # Battle records are produced only by the guarded GAME_OVER handler.
        terminal = "GAME_OVER"
        signals.append("record_identity:game_over")
    if tier is not None:
        signals.append(f"terminal_observation:tier_{tier}")

    farm_identity = profile == "farm" or family == "farm" or "farm" in strategy
    tournament_identity = (
        profile == "tournament"
        or family == "tournament"
        or strategy == "tournament"
    )
    if farm_identity:
        signals.append("strategy_identity:farm")
    if tournament_identity:
        signals.append("shared_loadout_identity:tournament_or_milestone")
    dissonance_identity = observed_identity_family == "dissonance"
    if dissonance_identity:
        subtype_signal = observed_identity_subtype.replace(" ", "_") or "unknown"
        signals.append(
            f"post_run_workshop_preset:{subtype_signal}_dissonance"
            if post_run_preset_identity
            else f"observed_identity:{subtype_signal}_dissonance"
        )

    if terminal == "TOURNAMENT_RESULTS":
        kind = "tournament"
        confidence = "high"
        reason = "The distinct Tournament Results terminal screen was detected."
    elif terminal == "GAME_OVER" and dissonance_identity:
        kind = "dissonance"
        confidence = "high"
        identity_label = observed_identity_label or "Dissonance"
        if post_run_preset_identity:
            reason = (
                "The run ended at Game Over and the immediately captured Home "
                f"Workshop preset identified {identity_label}."
            )
        else:
            reason = (
                "The run ended at Game Over after the fixed Tier badge "
                f"identified the {identity_label} modifier."
            )
    elif terminal == "GAME_OVER" and farm_identity:
        kind = "farm"
        confidence = "high"
        reason = "A Farm strategy/profile ended at the standard Game Over screen."
    elif terminal == "GAME_OVER" and tournament_identity:
        kind = "milestone"
        confidence = "high"
        reason = (
            "Tournament/Milestone settings were active, but the run ended at "
            "standard Game Over rather than Tournament Results."
        )
    elif farm_identity:
        kind = "farm"
        confidence = "medium"
        reason = "The strategy/profile identifies a Farm run."
    elif terminal == "GAME_OVER" and tier is not None:
        kind = "unknown"
        confidence = "low"
        reason = (
            f"The standard Game Over stats identify Tier {tier}, but Tier alone "
            "cannot distinguish a Farm run from a manual or Milestone run."
        )
    else:
        kind = "unknown"
        confidence = "low"
        reason = (
            "Tournament settings alone cannot distinguish Tournament from "
            "Milestone, and no authoritative terminal-screen evidence exists."
        )

    return {
        "type": kind,
        "label": (
            observed_identity_label or "Dissonance"
            if kind == "dissonance"
            else kind.title()
        ),
        "confidence": confidence,
        "reason": reason,
        "signals": signals,
        "observed_tier": tier,
    }


def classification_for_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return stored classification or analyze a historical record."""

    stored_type = str(record.get("battle_type") or "").strip().lower()
    stored_analysis = record.get("battle_type_analysis")
    observed_tier = observed_tier_for_record(record)
    observed_configuration = record.get("observed_run_configuration")
    observed_identity = _observed_run_identity(
        observed_configuration if isinstance(observed_configuration, Mapping) else None
    )
    needs_observed_reanalysis = bool(
        stored_type == "unknown"
        and str(observed_identity.get("family") or "").strip().lower()
        == "dissonance"
    )
    if (
        stored_type in KNOWN_BATTLE_TYPES
        and isinstance(stored_analysis, Mapping)
        and not needs_observed_reanalysis
    ):
        result = dict(stored_analysis)
        result["type"] = stored_type
        result.setdefault("label", stored_type.title())
        result.setdefault("observed_tier", observed_tier)
        if observed_tier is not None:
            signals = list(result.get("signals") or ())
            signal = f"terminal_observation:tier_{observed_tier}"
            if signal not in signals:
                signals.append(signal)
            result["signals"] = signals
        return result

    record_id = record.get("battle_id") or record.get("tournament_id")
    runtime = record.get("runtime")
    terminal_state = runtime.get("terminal_state") if isinstance(runtime, Mapping) else None
    configuration = record.get("run_configuration")
    return analyze_battle_type(
        strategy_name=record.get("strategy"),
        run_configuration=configuration if isinstance(configuration, Mapping) else {},
        terminal_state=terminal_state,
        record_id=str(record_id or ""),
        observed_tier=observed_tier,
        observed_run_configuration=(
            observed_configuration
            if isinstance(observed_configuration, Mapping)
            else None
        ),
    )


def _observed_run_identity(
    observed_run_configuration: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not isinstance(observed_run_configuration, Mapping):
        return {}
    fields = observed_run_configuration.get("fields")
    if not isinstance(fields, Mapping):
        return {}
    identity = fields.get("run_identity")
    if isinstance(identity, Mapping) and identity.get("status") == "observed":
        value = identity.get("value")
        if isinstance(value, Mapping):
            return value
    preset = fields.get("workshop_preset")
    if not isinstance(preset, Mapping) or preset.get("status") != "observed":
        return {}
    value = preset.get("value")
    label = str(value.get("label") or "") if isinstance(value, Mapping) else ""
    subtype = dissonance_subtype_from_preset_label(label)
    if subtype is None:
        return {}
    return {
        "family": "Dissonance",
        "subtype": subtype,
        "label": f"{subtype} Dissonance",
        "signals": {"post_run_workshop_preset": value},
    }


def observed_tier_for_record(record: Mapping[str, Any]) -> int | None:
    """Return Tier observed in terminal evidence, never configured intent."""

    runtime = record.get("runtime")
    if isinstance(runtime, Mapping):
        tier = _normalize_observed_tier(runtime.get("observed_tier"))
        if tier is not None:
            return tier

    game_stats = record.get("game_stats")
    if isinstance(game_stats, Mapping):
        fields = game_stats.get("fields")
        if isinstance(fields, Mapping):
            field = fields.get("tier")
            if isinstance(field, Mapping):
                tier = _normalize_observed_tier(
                    field.get("value", field.get("raw"))
                )
                if tier is not None:
                    return tier

    for source_key in ("more_stats", "detailed_stats"):
        source = record.get(source_key)
        if not isinstance(source, Mapping):
            continue
        for section in source.get("sections") or ():
            if (
                not isinstance(section, Mapping)
                or section.get("key") != "battle_report"
            ):
                continue
            for row in section.get("rows") or ():
                if not isinstance(row, Mapping) or row.get("key") != "tier":
                    continue
                tier = _normalize_observed_tier(
                    row.get("value", row.get("value_raw"))
                )
                if tier is not None:
                    return tier
    return None


def _normalize_observed_tier(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace(",", "").removesuffix("+")
    try:
        parsed = int(normalized)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "KNOWN_BATTLE_TYPES",
    "UNBOUND_RUN_EVIDENCE_WARNING",
    "analyze_battle_type",
    "classification_for_record",
    "dissonance_subtype_from_preset_label",
    "observed_tier_for_record",
    "unbound_run_evidence_warning",
]
