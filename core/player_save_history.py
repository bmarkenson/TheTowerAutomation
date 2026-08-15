"""Typed Battle History and running-attachment projections from save bundles.

This module never acquires a save or navigates game UI. Callers supply a typed
bundle acquired at its causal boundary; canonical active-round identity is
validated independently before these reporting/configuration projections run.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
)
from core.player_save_temporal import (
    RunningAttachmentSaveFact,
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
    attachment_temporal_class,
)


PLAYER_SAVE_HISTORY_SOURCE = "player_save"
BATTLE_HISTORY_UI_SOURCE = "battle_history_ui"
BATTLE_HISTORY_UI_MAPPING_ID = "battle-history-ui-report-v1"
PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION = 2
BATTLE_HISTORY_UI_IDENTITY_SCHEMA_VERSION = 1
ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION = 2
_UI_BATTLE_DATE_PATTERN = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(0[1-9]|[1-9]|[12][0-9]|3[01]), ([0-9]{4}) "
    r"([01][0-9]|2[0-3]):([0-5][0-9])$"
)
_MONTH_NUMBER = {
    month: index
    for index, month in enumerate(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        start=1,
    )
}


class PlayerSaveHistoryReadStatus(str, Enum):
    COMPLETE = "complete"
    UI_FALLBACK = "ui_fallback"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PlayerSaveHistoryReadResult:
    status: PlayerSaveHistoryReadStatus
    reason: str
    metadata: Optional[Mapping[str, Any]] = None
    safe_ui_fallback: bool = False
    running_attachment_observations: Optional[
        RunningAttachmentSaveObservations
    ] = field(
        default=None,
        repr=False,
        compare=False,
    )
    running_attachment_temporal_binding: Optional[
        RunningAttachmentTemporalBinding
    ] = field(default=None, repr=False, compare=False)
    running_attachment_context: Optional[
        "PlayerSaveAttachmentContext"
    ] = field(default=None, repr=False, compare=False)
    acquisition: Optional[PlayerSaveAcquisitionBundle] = field(
        default=None,
        repr=False,
        compare=False,
    )
    background_dispatched: bool = False
    operator_workflow_interrupted: bool = False
    source_restored: bool = True

    @property
    def complete(self) -> bool:
        return bool(
            self.status is PlayerSaveHistoryReadStatus.COMPLETE
            and self.metadata is not None
        )


@dataclass(frozen=True)
class PlayerSaveAttachmentContext:
    """Exact process, battle ID, and target for one attachment read."""

    runtime_session_id: str
    activity_scope_id: str = field(compare=False)
    active_round_identity_fingerprint: str
    target: str
    target_generation: int
    active_battle_observed: bool

    def valid_for(self, expected_scope_id: str) -> bool:
        del expected_scope_id
        return bool(
            self.runtime_session_id
            and self.active_round_identity_fingerprint
            and self.target
            and self.target_generation > 0
            and self.active_battle_observed
        )

    def target_generation_detail(self) -> str:
        return hashlib.sha256(
            f"{self.target}\0{self.target_generation}".encode("utf-8")
        ).hexdigest()[:16]


class CrossSourceHistoryStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class CrossSourceHistoryResult:
    status: CrossSourceHistoryStatus
    reason: str

    @property
    def matched(self) -> bool:
        return self.status is CrossSourceHistoryStatus.MATCH


def history_metadata_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
) -> PlayerSaveHistoryReadResult:
    """Project the structural newest History tail needed by reporting."""

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("History projection requires a typed acquisition")
    snapshot = acquisition.snapshot
    if not acquisition.complete or snapshot is None:
        return _ui_fallback(acquisition.reason, acquisition=acquisition)

    runtime = snapshot.runtime_save
    if runtime is None:
        return _ui_fallback("runtime_history_projection_unavailable")
    tail = runtime.battle_history_tail
    identity = tail.identity
    if tail.structural_status != "observed" or identity is None:
        return _ui_fallback(
            tail.structural_reason or "history_tail_identity_unavailable"
        )
    battle_date = getattr(identity, "battle_date", None)
    if (
        tail.capacity <= 0
        or tail.entry_count <= 0
        or tail.entry_count > tail.capacity
        or not identity.fingerprint
        or identity.mapping_id != runtime.mapping_id
        or not isinstance(battle_date, Mapping)
    ):
        return _ui_fallback("history_tail_structure_invalid")

    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.COMPLETE,
        "structural_history_tail_observed",
        metadata={
            "schema_version": ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION,
            "source": PLAYER_SAVE_HISTORY_SOURCE,
            "mapping_id": identity.mapping_id,
            "effective_mapping_fingerprint": (
                snapshot.effective_mapping_fingerprint
            ),
            "identity_schema_version": (
                PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION
            ),
            "fingerprint": identity.fingerprint,
            "tier": identity.tier,
            "wave": identity.wave,
            "is_tournament": identity.is_tournament,
            "battle_date": dict(battle_date),
            "entry_count": tail.entry_count,
            "capacity": tail.capacity,
            "semantic_status": tail.completed_entry_status,
            "semantic_reason": _safe_reason(tail.completed_entry_reason),
            "captured_at": snapshot.captured_at,
            "acquisition": acquisition.redacted_provenance(),
        },
        acquisition=acquisition,
    )


def running_attachment_observations_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    context: PlayerSaveAttachmentContext,
    active_round_identity_fingerprint: str,
) -> Optional[RunningAttachmentSaveObservations]:
    """Project only complete, allowlisted configuration observations.

    This is an observation projection, not a requirement reconciliation.  It is
    suitable for an attached No Strategy run because the guarded serializer has
    already established snapshot freshness and active-round identity.  Unknown,
    incomplete, or candidate checks outside the resolved mapping's validation
    allowlist are omitted rather than converted into UI claims.
    """

    binding = running_attachment_temporal_binding_from_acquisition(
        acquisition,
        context=context,
        active_round_identity_fingerprint=active_round_identity_fingerprint,
    )
    if binding is None:
        return None
    snapshot = acquisition.snapshot
    if not acquisition.complete or snapshot is None:
        return None
    mapping_id = str(getattr(snapshot, "mapping_id", None) or "").strip()
    mapping_maturity = str(
        getattr(snapshot, "mapping_maturity", None) or ""
    ).strip()
    captured_at = acquisition.captured_at.isoformat()
    checks = getattr(snapshot, "checks", None)
    validated = {
        str(check_id)
        for check_id in getattr(snapshot, "validated_checks", ()) or ()
    }
    if (
        not mapping_id
        or not captured_at
        or getattr(snapshot, "shape_valid", False) is not True
        or not isinstance(checks, Mapping)
    ):
        return None

    projected: list[RunningAttachmentSaveFact] = []
    for check_id, evidence in checks.items():
        normalized_id = str(check_id)
        if mapping_maturity != "validated" and normalized_id not in validated:
            continue
        if (
            getattr(evidence, "status", None) != "observed"
            or getattr(evidence, "complete", None) is not True
        ):
            continue
        projected.append(
            RunningAttachmentSaveFact(
                check_id=normalized_id,
                temporal_class=attachment_temporal_class(normalized_id),
                value=deepcopy(getattr(evidence, "value", None)),
                source_fields=tuple(
                    str(value)
                    for value in getattr(evidence, "source_fields", ()) or ()
                ),
            )
        )
    if not projected:
        return None
    return RunningAttachmentSaveObservations(
        binding=binding,
        facts=tuple(projected),
    )


def running_attachment_temporal_binding_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    context: PlayerSaveAttachmentContext,
    active_round_identity_fingerprint: str,
) -> Optional[RunningAttachmentTemporalBinding]:
    """Retain typed round identity even when no configuration fact projects."""

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("attachment binding requires a typed acquisition")
    if (
        acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        or acquisition.binding is None
        or acquisition.captured_at is None
        or context.target != acquisition.binding.target
        or context.target_generation != acquisition.binding.generation
        or not context.valid_for(context.activity_scope_id)
        or not str(active_round_identity_fingerprint or "").strip()
        or context.active_round_identity_fingerprint
        != str(active_round_identity_fingerprint).strip()
        or not acquisition.complete
        or acquisition.snapshot is None
    ):
        return None
    mapping_id = str(
        getattr(acquisition.snapshot, "mapping_id", None) or ""
    ).strip()
    effective_mapping_fingerprint = str(
        getattr(
            acquisition.snapshot,
            "effective_mapping_fingerprint",
            None,
        )
        or ""
    ).strip()
    if not mapping_id or len(effective_mapping_fingerprint) != 64:
        return None
    return RunningAttachmentTemporalBinding(
        runtime_session_id=context.runtime_session_id,
        source_activity_scope_id=context.activity_scope_id,
        target_binding=acquisition.binding,
        mapping_id=mapping_id,
        effective_mapping_fingerprint=effective_mapping_fingerprint,
        active_round_identity_fingerprint=(
            str(active_round_identity_fingerprint).strip()
        ),
        captured_at=acquisition.captured_at.isoformat(),
        acquisition_type=acquisition.acquisition_type,
    )


def ui_history_bridge_eligible(metadata: Mapping[str, Any]) -> bool:
    """Return whether retained UI fields can enter cross-source corroboration."""

    return bool(
        metadata.get("schema_version")
        == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and metadata.get("source") == BATTLE_HISTORY_UI_SOURCE
        and metadata.get("mapping_id") == BATTLE_HISTORY_UI_MAPPING_ID
        and metadata.get("identity_schema_version")
        == BATTLE_HISTORY_UI_IDENTITY_SCHEMA_VERSION
        and _positive_int(metadata.get("tier")) is not None
        and _positive_int(metadata.get("wave")) is not None
        and _parse_ui_battle_date(metadata.get("battle_date")) is not None
    )


def corroborate_ui_and_save_history(
    ui_metadata: Mapping[str, Any],
    save_metadata: Mapping[str, Any],
) -> CrossSourceHistoryResult:
    """Bridge source contracts through Tier/Wave/date, never fingerprints."""

    if not ui_history_bridge_eligible(ui_metadata):
        return _cross_source_ambiguous("ui_history_identity_insufficient")
    if not (
        save_metadata.get("schema_version")
        == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and save_metadata.get("source") == PLAYER_SAVE_HISTORY_SOURCE
        and str(save_metadata.get("mapping_id") or "")
        and save_metadata.get("identity_schema_version")
        == PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION
    ):
        return _cross_source_ambiguous("save_history_identity_insufficient")

    ui_tier = _positive_int(ui_metadata.get("tier"))
    ui_wave = _positive_int(ui_metadata.get("wave"))
    save_tier = _positive_int(save_metadata.get("tier"))
    save_wave = _positive_int(save_metadata.get("wave"))
    if save_tier is None or save_wave is None:
        return _cross_source_ambiguous("save_history_tier_wave_ambiguous")
    if ui_tier != save_tier or ui_wave != save_wave:
        return CrossSourceHistoryResult(
            CrossSourceHistoryStatus.MISMATCH,
            "cross_source_tier_wave_mismatch",
        )

    ui_date = _parse_ui_battle_date(ui_metadata.get("battle_date"))
    save_date = _parse_unambiguous_local_save_date(
        save_metadata.get("battle_date")
    )
    if ui_date is None or save_date is None:
        return _cross_source_ambiguous("cross_source_battle_date_ambiguous")
    if ui_date != save_date.replace(second=0, microsecond=0):
        return CrossSourceHistoryResult(
            CrossSourceHistoryStatus.MISMATCH,
            "cross_source_battle_date_mismatch",
        )
    return CrossSourceHistoryResult(
        CrossSourceHistoryStatus.MATCH,
        "cross_source_tier_wave_battle_date_match",
    )


def history_sources_compatible(
    first: Optional[Mapping[str, Any]],
    second: Optional[Mapping[str, Any]],
) -> bool:
    """Return whether two fingerprints share one proven source contract."""

    if first is None or second is None:
        return False
    return bool(
        first.get("schema_version") == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and second.get("schema_version")
        == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and str(first.get("source") or "")
        == str(second.get("source") or "")
        and str(first.get("mapping_id") or "")
        == str(second.get("mapping_id") or "")
        and first.get("identity_schema_version")
        == second.get("identity_schema_version")
        and (
            str(first.get("source") or "") != PLAYER_SAVE_HISTORY_SOURCE
            or (
                str(first.get("effective_mapping_fingerprint") or "")
                and str(first.get("effective_mapping_fingerprint") or "")
                == str(second.get("effective_mapping_fingerprint") or "")
            )
        )
        and str(first.get("fingerprint") or "")
        and str(second.get("fingerprint") or "")
    )


def valid_history_tail_advance(
    previous: Mapping[str, Any],
    latest: Mapping[str, Any],
) -> bool:
    """Validate one append or a capacity-preserving 30-entry rollover."""

    if not history_sources_compatible(previous, latest):
        return False
    try:
        previous_count = int(previous["entry_count"])
        latest_count = int(latest["entry_count"])
        previous_capacity = int(previous["capacity"])
        latest_capacity = int(latest["capacity"])
    except (KeyError, TypeError, ValueError):
        return False
    if (
        previous_capacity <= 0
        or latest_capacity != previous_capacity
        or not 0 < previous_count <= previous_capacity
        or not 0 < latest_count <= latest_capacity
        or previous.get("fingerprint") == latest.get("fingerprint")
    ):
        return False
    if previous_count < previous_capacity:
        return latest_count == previous_count + 1
    return latest_count == previous_capacity


def _safe_reason(value: Any) -> str:
    normalized = "_".join(str(value or "").strip().lower().split())
    return "".join(
        character
        for character in normalized[:160]
        if character.isalnum() or character in {"_", ":", "-"}
    )


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[1-9][0-9]*", value) is None
    ):
        return None
    return int(value)


def _parse_ui_battle_date(value: Any) -> Optional[datetime]:
    match = _UI_BATTLE_DATE_PATTERN.fullmatch(str(value or "").strip())
    if match is None:
        return None
    month, day, year, hour, minute = match.groups()
    try:
        return datetime(
            int(year),
            _MONTH_NUMBER[month],
            int(day),
            int(hour),
            int(minute),
        )
    except (KeyError, ValueError):
        return None


def _parse_unambiguous_local_save_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, Mapping):
        return None
    if (
        value.get("kind_id") != 2
        or value.get("kind") != "local"
        or value.get("clock_basis") != "local_wall_clock_without_offset"
    ):
        return None
    ticks = str(value.get("ticks") or "")
    if not ticks.isascii() or not ticks.isdigit():
        return None
    try:
        submicrosecond = int(value.get("submicrosecond_100ns"))
    except (TypeError, ValueError):
        return None
    if not 0 <= submicrosecond <= 9:
        return None
    try:
        parsed = datetime.fromisoformat(str(value.get("clock_time") or ""))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return None
    return parsed


def _cross_source_ambiguous(reason: str) -> CrossSourceHistoryResult:
    return CrossSourceHistoryResult(
        CrossSourceHistoryStatus.AMBIGUOUS,
        reason,
    )


def _ui_fallback(
    reason: str,
    *,
    acquisition: Optional[PlayerSaveAcquisitionBundle] = None,
) -> PlayerSaveHistoryReadResult:
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.UI_FALLBACK,
        _safe_reason(reason),
        safe_ui_fallback=True,
        acquisition=acquisition,
    )


__all__ = [
    "ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION",
    "BATTLE_HISTORY_UI_MAPPING_ID",
    "BATTLE_HISTORY_UI_SOURCE",
    "CrossSourceHistoryResult",
    "CrossSourceHistoryStatus",
    "PLAYER_SAVE_HISTORY_SOURCE",
    "PlayerSaveAttachmentContext",
    "PlayerSaveHistoryReadResult",
    "PlayerSaveHistoryReadStatus",
    "corroborate_ui_and_save_history",
    "history_metadata_from_acquisition",
    "history_sources_compatible",
    "ui_history_bridge_eligible",
    "running_attachment_observations_from_acquisition",
    "running_attachment_temporal_binding_from_acquisition",
    "valid_history_tail_advance",
]
