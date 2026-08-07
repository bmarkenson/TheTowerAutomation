"""Source-bound Battle History continuity from stable player-save reads.

Ordinary reads remain observation-only.  Replacement-process attachment may
explicitly request the guarded Android-Home serialization shared with Home
preflight.  Neither path navigates game UI or authorizes lifecycle input, and
``UI_FALLBACK`` is available only after the exact source was safely restored.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import re
import time
from typing import Any, Callable, Mapping, Optional

from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import detect_home_battle_control
from core.player_save import (
    PlayerSaveSnapshot,
    decode_player_save_bytes,
    pull_player_save_bytes,
)
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    StablePlayerSaveAcquirer,
)
from core.player_save_temporal import (
    RunningAttachmentSaveFact,
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
    attachment_temporal_class,
)
from core.state_detector import detect_state_and_overlays
from utils.logger import get_activity_scope, log, log_input


PLAYER_SAVE_HISTORY_SOURCE = "player_save"
BATTLE_HISTORY_UI_SOURCE = "battle_history_ui"
BATTLE_HISTORY_UI_MAPPING_ID = "battle-history-ui-report-v1"
PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION = 1
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
    acquisition: Optional[PlayerSaveAcquisitionBundle] = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def complete(self) -> bool:
        return bool(
            self.status is PlayerSaveHistoryReadStatus.COMPLETE
            and self.metadata is not None
        )


@dataclass(frozen=True)
class PlayerSaveAttachmentContext:
    """Exact process-local authority for one running-battle attachment read."""

    runtime_session_id: str
    activity_scope_id: str
    target: str
    target_generation: int
    active_battle_observed: bool

    def valid_for(self, expected_scope_id: str) -> bool:
        return bool(
            self.runtime_session_id
            and self.activity_scope_id == str(expected_scope_id or "")
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
    """Project only the structural newest-tail identity needed by continuity."""

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
            "identity_schema_version": (
                PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION
            ),
            "fingerprint": identity.fingerprint,
            "tier": identity.tier,
            "wave": identity.wave,
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
    incomplete, or candidate checks outside the exact mapping's validation
    allowlist are omitted rather than converted into UI claims.
    """

    if not isinstance(acquisition, PlayerSaveAcquisitionBundle):
        raise TypeError("profile projection requires a typed acquisition")
    if (
        acquisition.acquisition_type
        is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        or acquisition.binding is None
        or acquisition.captured_at is None
        or context.target != acquisition.binding.target
        or context.target_generation != acquisition.binding.generation
        or not context.valid_for(context.activity_scope_id)
        or not str(active_round_identity_fingerprint or "").strip()
    ):
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
        binding=RunningAttachmentTemporalBinding(
            runtime_session_id=context.runtime_session_id,
            source_activity_scope_id=context.activity_scope_id,
            target_binding=acquisition.binding,
            mapping_id=mapping_id,
            active_round_identity_fingerprint=(
                active_round_identity_fingerprint
            ),
            captured_at=captured_at,
            acquisition_type=acquisition.acquisition_type,
        ),
        facts=tuple(projected),
    )


def ui_history_bridge_eligible(metadata: Mapping[str, Any]) -> bool:
    """Return whether retained UI fields can enter cross-source corroboration."""

    return bool(
        metadata.get("schema_version")
        == ACTIVITY_HISTORY_METADATA_SCHEMA_VERSION
        and metadata.get("source") == BATTLE_HISTORY_UI_SOURCE
        and metadata.get("mapping_id") == BATTLE_HISTORY_UI_MAPPING_ID
        and metadata.get("identity_schema_version")
        == PLAYER_SAVE_HISTORY_IDENTITY_SCHEMA_VERSION
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


class PlayerSaveHistoryReader:
    """Acquire one stable save while preserving exact runtime source binding."""

    def __init__(
        self,
        *,
        target_snapshot_fn: Callable[[], AdbTargetSnapshot],
        capture_fn: Callable[[], Any],
        detector: Callable[[Any], Mapping[str, Any]] = (
            detect_state_and_overlays
        ),
        home_control_fn: Callable[[Any], Any] = detect_home_battle_control,
        scope_fn: Callable[[], Optional[Mapping[str, Any]]] = get_activity_scope,
        attachment_context_fn: Optional[
            Callable[[], PlayerSaveAttachmentContext]
        ] = None,
        background_fn: Optional[Callable[[str], bool]] = None,
        foreground_fn: Optional[Callable[[str], bool]] = None,
        pull_fn: Callable[..., bytes] = pull_player_save_bytes,
        decode_fn: Callable[..., PlayerSaveSnapshot] = decode_player_save_bytes,
        acquirer: Optional[StablePlayerSaveAcquirer] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        input_log_fn: Callable[..., None] = log_input,
        debug_log_fn: Callable[..., None] = log,
    ) -> None:
        self._target_snapshot_fn = target_snapshot_fn
        self._capture_fn = capture_fn
        self._detector = detector
        self._home_control_fn = home_control_fn
        self._scope_fn = scope_fn
        self._attachment_context_fn = attachment_context_fn
        self._background_fn = background_fn
        self._foreground_fn = foreground_fn
        self._pull_fn = pull_fn
        self._decode_fn = decode_fn
        self._acquirer = acquirer or StablePlayerSaveAcquirer(
            target_snapshot_fn=target_snapshot_fn,
            pull_fn=pull_fn,
            decode_fn=decode_fn,
        )
        self._sleep_fn = sleep_fn
        self._input_log_fn = input_log_fn
        self._debug_log_fn = debug_log_fn

    def read(
        self,
        *,
        source_state: str,
        expected_home_control: HomeBattleControl,
        expected_scope_id: str,
        action_guard_fn: Callable[[], bool],
        serialize_active_attachment: bool = False,
    ) -> PlayerSaveHistoryReadResult:
        normalized_source = str(source_state or "").upper()
        if normalized_source not in {"RUNNING", "HOME_SCREEN"}:
            return _blocked("save_history_source_unsupported")
        if serialize_active_attachment:
            if normalized_source != "RUNNING":
                return _blocked("active_attachment_source_unsupported")
            return self._read_serialized_active_attachment(
                expected_scope_id=expected_scope_id,
                action_guard_fn=action_guard_fn,
            )

        with self._acquirer.locked_operation():
            binding = self._acquirer.current_binding()
            if (
                binding is None
                or not _scope_matches(self._scope_fn, expected_scope_id)
                or not _action_allowed(action_guard_fn)
                or not self._source_matches(
                    normalized_source,
                    expected_home_control,
                )
            ):
                return _blocked("history_source_binding_unverified")

            acquisition = self._acquirer.acquire(
                PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
                expected_binding=binding,
            )
            if acquisition.complete:
                try:
                    observed = history_metadata_from_acquisition(acquisition)
                except Exception:
                    observed = _ui_fallback(
                        "runtime_history_projection_unavailable",
                        acquisition=acquisition,
                    )
            else:
                observed = _ui_fallback(
                    "save_history_acquisition_failed",
                    acquisition=acquisition,
                )

            if (
                acquisition.status
                in {
                    PlayerSaveAcquisitionStatus.BINDING_REJECTED,
                    PlayerSaveAcquisitionStatus.BINDING_LOST,
                }
                or not self._acquirer.binding_matches(binding)
                or not _scope_matches(self._scope_fn, expected_scope_id)
                or not _action_allowed(action_guard_fn)
                or not self._source_matches(
                    normalized_source,
                    expected_home_control,
                )
            ):
                return _blocked("history_source_binding_lost")
            return observed

    def _read_serialized_active_attachment(
        self,
        *,
        expected_scope_id: str,
        action_guard_fn: Callable[[], bool],
    ) -> PlayerSaveHistoryReadResult:
        context_fn = self._attachment_context_fn
        if context_fn is None:
            return _blocked("active_attachment_context_unavailable")
        try:
            context = context_fn()
        except Exception:
            return _blocked("active_attachment_context_unavailable")
        if not context.valid_for(expected_scope_id):
            return _blocked("active_attachment_context_unverified")

        serializer = GuardedPlayerSaveSerializer(
            target_snapshot_fn=self._target_snapshot_fn,
            context_guard_fn=lambda: self._same_attachment_context(
                context,
                expected_scope_id,
            ),
            action_guard_fn=action_guard_fn,
            source_guard_fn=lambda frame, stable: self._source_matches(
                "RUNNING",
                HomeBattleControl.UNKNOWN,
                initial_frame=frame,
                stable=stable,
            ),
            background_fn=self._background_fn,
            foreground_fn=self._foreground_fn,
            pull_fn=self._pull_fn,
            decode_fn=self._decode_fn,
            acquirer=self._acquirer,
            sleep_fn=self._sleep_fn,
            input_log_fn=self._input_log_fn,
            debug_log_fn=self._debug_log_fn,
            log_prefix="BATTLE_CONTINUITY",
        )
        serialized = serializer.acquire(
            expected_target=context.target,
            expected_generation=context.target_generation,
            target_generation_detail=context.target_generation_detail(),
            source_label="the attached running battle",
            stable_initial_source=True,
        )
        if serialized.status is GuardedSerializationStatus.BLOCKED:
            return _blocked(
                f"active_attachment_{serialized.reason}"
            )
        acquisition = serialized.acquisition
        snapshot = serialized.snapshot
        if snapshot is None:
            return _ui_fallback(serialized.reason, acquisition=acquisition)

        try:
            runtime = snapshot.runtime_save
        except Exception:
            return _ui_fallback(
                "active_round_projection_unavailable",
                acquisition=acquisition,
            )
        if runtime is None:
            return _ui_fallback("active_round_projection_unavailable")
        active_identity = runtime.active_round_identity
        if not runtime.round_active or active_identity is None:
            return _blocked("active_round_identity_conflicted_after_restore")
        if (
            not active_identity.fingerprint
            or active_identity.game_version != snapshot.game_version
            or active_identity.current_tier < 0
            or active_identity.rounds_started_this_tier < 0
            or active_identity.round_seed <= 0
        ):
            return _blocked("active_round_identity_invalid_after_restore")

        assert acquisition is not None
        try:
            observed = history_metadata_from_acquisition(acquisition)
        except Exception:
            observed = _ui_fallback(
                "runtime_history_projection_unavailable",
                acquisition=acquisition,
            )
        try:
            attachment_observations = (
                running_attachment_observations_from_acquisition(
                    acquisition,
                    context=context,
                    active_round_identity_fingerprint=(
                        active_identity.fingerprint
                    ),
                )
            )
        except Exception:
            attachment_observations = None
        return PlayerSaveHistoryReadResult(
            observed.status,
            observed.reason,
            metadata=observed.metadata,
            safe_ui_fallback=observed.safe_ui_fallback,
            running_attachment_observations=attachment_observations,
            acquisition=acquisition,
        )

    def _same_attachment_context(
        self,
        expected: PlayerSaveAttachmentContext,
        expected_scope_id: str,
    ) -> bool:
        context_fn = self._attachment_context_fn
        if context_fn is None:
            return False
        try:
            current = context_fn()
        except Exception:
            return False
        return bool(
            current == expected
            and current.valid_for(expected_scope_id)
            and _scope_matches(self._scope_fn, expected_scope_id)
        )

    def _source_matches(
        self,
        source_state: str,
        expected_home_control: HomeBattleControl,
        *,
        initial_frame: Any = None,
        stable: bool = False,
    ) -> bool:
        attempts = 2 if stable else 1
        frame = initial_frame
        for attempt in range(attempts):
            try:
                if frame is None or attempt > 0:
                    frame = self._capture_fn()
                if frame is None:
                    return False
                detection = self._detector(frame)
                state = str(detection.get("state") or "").upper()
                if source_state == "RUNNING":
                    matched = state == "RUNNING"
                else:
                    matched = bool(
                        state in {"HOME", "HOME_SCREEN"}
                        and self._home_control_fn(frame).control
                        is expected_home_control
                    )
                if not matched:
                    return False
            except Exception:
                return False
            if stable and attempt == 0:
                self._sleep_fn(0.2)
        return True


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


def _scope_matches(
    scope_fn: Callable[[], Optional[Mapping[str, Any]]],
    expected_scope_id: str,
) -> bool:
    try:
        scope = scope_fn()
    except Exception:
        return False
    return bool(
        isinstance(scope, Mapping)
        and str(scope.get("run_id") or "") == str(expected_scope_id or "")
    )


def _action_allowed(action_guard_fn: Callable[[], bool]) -> bool:
    try:
        return action_guard_fn() is True
    except Exception:
        return False


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


def _blocked(reason: str) -> PlayerSaveHistoryReadResult:
    return PlayerSaveHistoryReadResult(
        PlayerSaveHistoryReadStatus.BLOCKED,
        _safe_reason(reason),
        safe_ui_fallback=False,
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
    "PlayerSaveHistoryReader",
    "corroborate_ui_and_save_history",
    "history_metadata_from_acquisition",
    "history_sources_compatible",
    "ui_history_bridge_eligible",
    "running_attachment_observations_from_acquisition",
    "valid_history_tail_advance",
]
