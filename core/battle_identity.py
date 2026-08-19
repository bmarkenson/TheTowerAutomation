"""Canonical save-backed identity for the battle currently on screen.

``ActiveRoundIdentity`` from the decoded player save is the only durable
same-battle key.  A launch/workflow operation ID may guard the short interval
before that identity exists, but log cursors and UI activity scopes never grant
battle authority here.

The coordinator deliberately forces serialization before reading identity.
It never waits for a naturally updated save and it never performs a passive
fallback.  Perk monitoring owns the one intentionally passive save path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Mapping, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_serialization import (
    GuardedPlayerSaveSerializer,
    GuardedSerializationStatus,
)
from core.battle_activation_tracker import (
    battle_activation_checkpoint_configuration_fingerprint,
    battle_activation_snapshot_from_checkpoint,
)
from core.runtime_save import (
    ActiveRoundIdentity,
    RoundCounterVectorEvidence,
)


BATTLE_IDENTITY_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class BattleIdentityStoreError(RuntimeError):
    """The durable battle-identity record could not be trusted or written."""


class BattleIdentityContinuityError(BattleIdentityStoreError):
    """An active-battle host handoff could not prove save continuity."""

    def __init__(self, reason: str) -> None:
        normalized = "_".join(str(reason or "").strip().lower().split())
        self.reason = normalized or "emulator_handoff_continuity_unavailable"
        super().__init__(self.reason)


class BattleIdentityRelation(str, Enum):
    """How one forced active identity relates to retained durable state."""

    FIRST_OBSERVATION = "first_observation"
    SAME_BATTLE = "same_battle"
    LATER_BATTLE = "later_battle"


class BattleIdentityCheckStatus(str, Enum):
    """Outcome class independent of the caller's runtime policy."""

    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


@dataclass(frozen=True, repr=False)
class BattleIdentityCheckContext:
    """Process-local owner of one forced identity check."""

    runtime_session_id: str = field(repr=False)
    operation_id: str = field(repr=False)
    target_binding: PlayerSaveTargetBinding = field(repr=False)

    def __post_init__(self) -> None:
        runtime_session_id = str(self.runtime_session_id or "").strip()
        operation_id = str(self.operation_id or "").strip()
        if not runtime_session_id:
            raise ValueError("battle identity requires a runtime session")
        if not operation_id:
            raise ValueError("battle identity requires an operation ID")
        if not isinstance(self.target_binding, PlayerSaveTargetBinding):
            raise TypeError("battle identity requires a typed target binding")
        object.__setattr__(self, "runtime_session_id", runtime_session_id)
        object.__setattr__(self, "operation_id", operation_id)

    def __repr__(self) -> str:
        return (
            "BattleIdentityCheckContext("
            f"target='{self.target_binding.fingerprint[:16]}...')"
        )


@dataclass(frozen=True)
class ActiveBattleTerminalContinuity:
    """Save facts retained to recognize this battle after it becomes inactive."""

    round_counter_vector_fingerprint: str
    round_counter_tier_count: int
    save_revision: int
    target_binding_fingerprint: str

    def __post_init__(self) -> None:
        if (
            _SHA256_RE.fullmatch(self.round_counter_vector_fingerprint) is None
            or _SHA256_RE.fullmatch(self.target_binding_fingerprint) is None
            or type(self.round_counter_tier_count) is not int
            or self.round_counter_tier_count <= 0
            or type(self.save_revision) is not int
            or self.save_revision < 0
        ):
            raise ValueError("battle terminal continuity is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "round_counter_vector_fingerprint": (
                self.round_counter_vector_fingerprint
            ),
            "round_counter_tier_count": self.round_counter_tier_count,
            "save_revision": self.save_revision,
            "target_binding_fingerprint": self.target_binding_fingerprint,
        }


@dataclass(frozen=True)
class ActiveBattleProgressCheckpoint:
    """Monotonic save facts retained for one active battle."""

    max_save_revision: int
    max_current_wave: int
    updated_at: str
    target_binding_fingerprint: str

    def __post_init__(self) -> None:
        try:
            parsed = datetime.fromisoformat(self.updated_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("battle progress checkpoint is invalid") from exc
        if (
            type(self.max_save_revision) is not int
            or self.max_save_revision < 0
            or type(self.max_current_wave) is not int
            or self.max_current_wave < 0
            or parsed.tzinfo is None
            or _SHA256_RE.fullmatch(self.target_binding_fingerprint) is None
        ):
            raise ValueError("battle progress checkpoint is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "max_save_revision": self.max_save_revision,
            "max_current_wave": self.max_current_wave,
            "updated_at": self.updated_at,
            "target_binding_fingerprint": self.target_binding_fingerprint,
        }


@dataclass(frozen=True)
class ActiveBattleEmulatorHandoffGuard:
    """One destination that must prove the retained active battle."""

    request_id: str
    identity_fingerprint: str
    source_target_binding_fingerprint: str
    destination_target_binding_fingerprint: str
    source_save_revision: Optional[int]
    source_wave: Optional[int]
    armed_at: str
    status: str = "armed"
    failure_reason: Optional[str] = None
    observed_save_revision: Optional[int] = None
    observed_wave: Optional[int] = None
    detected_at: Optional[str] = None

    def __post_init__(self) -> None:
        try:
            armed = datetime.fromisoformat(self.armed_at)
            detected = (
                datetime.fromisoformat(self.detected_at)
                if self.detected_at is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("emulator handoff guard is invalid") from exc
        revision_valid = bool(
            self.source_save_revision is None
            or (
                type(self.source_save_revision) is int
                and self.source_save_revision >= 0
            )
        )
        wave_valid = bool(
            self.source_wave is None
            or (type(self.source_wave) is int and self.source_wave >= 0)
        )
        observations_valid = bool(
            (
                self.observed_save_revision is None
                or (
                    type(self.observed_save_revision) is int
                    and self.observed_save_revision >= 0
                )
            )
            and (
                self.observed_wave is None
                or (
                    type(self.observed_wave) is int
                    and self.observed_wave >= 0
                )
            )
        )
        if (
            not str(self.request_id or "").strip()
            or len(self.request_id) > 128
            or _SHA256_RE.fullmatch(self.identity_fingerprint) is None
            or _SHA256_RE.fullmatch(
                self.source_target_binding_fingerprint
            )
            is None
            or _SHA256_RE.fullmatch(
                self.destination_target_binding_fingerprint
            )
            is None
            or self.source_target_binding_fingerprint
            == self.destination_target_binding_fingerprint
            or not revision_valid
            or not wave_valid
            or (
                self.source_save_revision is None
                and self.source_wave is None
            )
            or armed.tzinfo is None
            or self.status not in {"armed", "blocked"}
            or not observations_valid
            or (
                self.status == "armed"
                and (
                    self.failure_reason is not None
                    or self.detected_at is not None
                    or self.observed_save_revision is not None
                    or self.observed_wave is not None
                )
            )
            or (
                self.status == "blocked"
                and (
                    not str(self.failure_reason or "").strip()
                    or detected is None
                    or detected.tzinfo is None
                )
            )
        ):
            raise ValueError("emulator handoff guard is invalid")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "request_id": self.request_id,
            "identity_fingerprint": self.identity_fingerprint,
            "source_target_binding_fingerprint": (
                self.source_target_binding_fingerprint
            ),
            "destination_target_binding_fingerprint": (
                self.destination_target_binding_fingerprint
            ),
            "source_save_revision": self.source_save_revision,
            "source_wave": self.source_wave,
            "armed_at": self.armed_at,
            "status": self.status,
        }
        if self.status == "blocked":
            payload.update(
                {
                    "failure_reason": self.failure_reason,
                    "observed_save_revision": self.observed_save_revision,
                    "observed_wave": self.observed_wave,
                    "detected_at": self.detected_at,
                }
            )
        return payload


@dataclass(frozen=True)
class ActiveBattleIdentityRecord:
    """Validated durable record for the last force-bound active battle."""

    identity: ActiveRoundIdentity
    bound_at: str
    reason: str
    operation_id: str = field(repr=False)
    acquisition: Mapping[str, Any] = field(default_factory=dict, repr=False)
    session_preflight: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )
    strategy_snapshot: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )
    operator_terminal_attestation: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )
    survival_activation_checkpoint: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )
    terminal_continuity: Optional[ActiveBattleTerminalContinuity] = field(
        default=None,
        repr=False,
    )
    progress_checkpoint: Optional[ActiveBattleProgressCheckpoint] = field(
        default=None,
        repr=False,
    )
    emulator_handoff_guard: Optional[
        ActiveBattleEmulatorHandoffGuard
    ] = field(
        default=None,
        repr=False,
    )

    @property
    def fingerprint(self) -> str:
        return self.identity.fingerprint


@dataclass(frozen=True)
class BattleIdentityCheckResult:
    status: BattleIdentityCheckStatus
    reason: str
    identity: Optional[ActiveRoundIdentity] = None
    relation: Optional[BattleIdentityRelation] = None
    acquisition: Optional[PlayerSaveAcquisitionBundle] = field(
        default=None,
        repr=False,
    )
    source_restored: bool = False
    lifecycle_input_attempted: bool = False

    @property
    def complete(self) -> bool:
        return bool(
            self.status is BattleIdentityCheckStatus.COMPLETE
            and self.identity is not None
            and self.relation is not None
            and self.acquisition is not None
            and self.acquisition.complete
        )

    @property
    def recapture_required(self) -> bool:
        return self.lifecycle_input_attempted


class BattleIdentityStore:
    """Atomic single-runtime persistence keyed by ``ActiveRoundIdentity``."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def active(self) -> Optional[ActiveBattleIdentityRecord]:
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return None
            return _record_from_payload(payload)

    def accept_expected_emulator_handoff_wave_rollback(
        self,
    ) -> Optional[ActiveBattleEmulatorHandoffGuard]:
        """Clear one legacy wave-only failure already proved at destination.

        A different PC resumes the newest cloud save, which can be behind the
        source PC even when ``ActiveRoundIdentity`` proves the same battle.
        Version 1 originally made that expected wave rollback sticky.  The
        stored failure reason is ordered evidence that identity, destination,
        revision availability, and non-regression all passed first.
        """

        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return None
            record = _record_from_payload(payload)
            guard = record.emulator_handoff_guard
            if not (
                guard is not None
                and guard.status == "blocked"
                and guard.failure_reason
                == "emulator_handoff_current_wave_regressed"
                and guard.source_wave is not None
                and guard.observed_wave is not None
                and guard.observed_wave < guard.source_wave
                and guard.source_save_revision is not None
                and guard.observed_save_revision is not None
                and guard.observed_save_revision
                >= guard.source_save_revision
            ):
                return None
            payload.pop("emulator_handoff_guard", None)
            self._write_payload(payload)
            return guard

    def bind(
        self,
        identity: ActiveRoundIdentity,
        *,
        reason: str,
        operation_id: str,
        acquisition: PlayerSaveAcquisitionBundle,
        bound_at: Optional[datetime] = None,
    ) -> tuple[ActiveBattleIdentityRecord, BattleIdentityRelation]:
        """Persist one freshly serialized identity and report continuity."""

        normalized_identity = _validated_identity(identity.as_dict())
        if normalized_identity is None:
            raise BattleIdentityStoreError("active round identity is invalid")
        normalized_reason = " ".join(str(reason or "").split())[:256]
        normalized_operation = str(operation_id or "").strip()[:128]
        if not normalized_reason or not normalized_operation:
            raise BattleIdentityStoreError(
                "battle identity reason and operation ID are required"
            )
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        ):
            raise BattleIdentityStoreError(
                "battle identity requires a complete forced serialization"
            )
        runtime = getattr(acquisition.snapshot, "runtime_save", None)
        observed_identity = getattr(runtime, "active_round_identity", None)
        if (
            getattr(runtime, "round_active", None) is not True
            or not isinstance(observed_identity, ActiveRoundIdentity)
            or observed_identity.fingerprint
            != normalized_identity.fingerprint
        ):
            raise BattleIdentityStoreError(
                "forced serialization does not contain the supplied battle identity"
            )
        terminal_continuity = _terminal_continuity_from_acquisition(
            acquisition
        )
        observed_progress = _progress_checkpoint_from_acquisition(
            acquisition,
            identity_fingerprint=normalized_identity.fingerprint,
            observed_at=bound_at,
        )
        timestamp = _aware_timestamp(bound_at).isoformat()

        with self._lock:
            try:
                previous_payload = self._read_payload()
            except BattleIdentityStoreError:
                # A fresh forced observation supersedes corrupt advisory state.
                previous_payload = None
            previous = (
                _record_from_payload(previous_payload)
                if previous_payload is not None
                and previous_payload.get("status") == "active"
                else None
            )
            if (
                previous is not None
                and previous.emulator_handoff_guard is not None
            ):
                self._verify_emulator_handoff_guard(
                    previous_payload,
                    previous.emulator_handoff_guard,
                    identity=normalized_identity,
                    acquisition=acquisition,
                )
            if previous is None:
                relation = BattleIdentityRelation.FIRST_OBSERVATION
            elif previous.fingerprint == normalized_identity.fingerprint:
                relation = BattleIdentityRelation.SAME_BATTLE
            else:
                relation = BattleIdentityRelation.LATER_BATTLE

            progress_checkpoint = observed_progress
            if (
                relation is BattleIdentityRelation.SAME_BATTLE
                and previous is not None
            ):
                progress_checkpoint = _merge_progress_checkpoints(
                    previous.progress_checkpoint,
                    observed_progress,
                )

            payload: dict[str, Any] = {
                "schema_version": BATTLE_IDENTITY_SCHEMA_VERSION,
                "status": "active",
                "identity": normalized_identity.as_dict(),
                "bound_at": timestamp,
                "reason": normalized_reason,
                "operation_id": normalized_operation,
                "acquisition": acquisition.redacted_provenance(),
            }
            if terminal_continuity is not None:
                payload["terminal_continuity"] = (
                    terminal_continuity.as_dict()
                )
            if progress_checkpoint is not None:
                payload["progress_checkpoint"] = (
                    progress_checkpoint.as_dict()
                )
            if (
                relation is BattleIdentityRelation.SAME_BATTLE
                and previous is not None
            ):
                for key, value in (
                    ("session_preflight", previous.session_preflight),
                    ("strategy_snapshot", previous.strategy_snapshot),
                    (
                        "operator_terminal_attestation",
                        previous.operator_terminal_attestation,
                    ),
                    (
                        "survival_activation_checkpoint",
                        previous.survival_activation_checkpoint,
                    ),
                ):
                    if value is not None:
                        payload[key] = dict(value)
            self._write_payload(payload)
            return _record_from_payload(payload), relation

    def record_progress_checkpoint(
        self,
        *,
        identity_fingerprint: str,
        target_binding: PlayerSaveTargetBinding,
        acquisition: PlayerSaveAcquisitionBundle,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Advance active-battle save high-water marks from a shared bundle."""

        expected = str(identity_fingerprint or "").strip()
        if (
            _SHA256_RE.fullmatch(expected) is None
            or not isinstance(target_binding, PlayerSaveTargetBinding)
            or not isinstance(acquisition, PlayerSaveAcquisitionBundle)
            or not acquisition.complete
            or not acquisition.matches_binding(target_binding)
        ):
            return False
        checkpoint = _progress_checkpoint_from_acquisition(
            acquisition,
            identity_fingerprint=expected,
            observed_at=observed_at,
        )
        if checkpoint is None:
            return False
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            if record.fingerprint != expected:
                return False
            guard = record.emulator_handoff_guard
            if guard is not None:
                # Freeze the captured source marks once handoff preparation
                # begins. Letting any late passive completion advance them, or
                # destination evidence replace them, would make the comparison
                # depend on a scheduler race.
                return False
            merged = _merge_progress_checkpoints(
                record.progress_checkpoint,
                checkpoint,
            )
            if merged == record.progress_checkpoint:
                return False
            payload["progress_checkpoint"] = merged.as_dict()
            self._write_payload(payload)
            return True

    def arm_emulator_handoff_guard(
        self,
        *,
        request_id: str,
        identity_fingerprint: str,
        source_target_binding: PlayerSaveTargetBinding,
        destination_target_binding: PlayerSaveTargetBinding,
        source_wave: Optional[int] = None,
        armed_at: Optional[datetime] = None,
    ) -> Optional[ActiveBattleEmulatorHandoffGuard]:
        """Bind one active-battle host move to its source high-water marks."""

        normalized_request = " ".join(str(request_id or "").split())[:128]
        expected = str(identity_fingerprint or "").strip()
        normalized_wave = (
            source_wave
            if type(source_wave) is int and source_wave >= 0
            else None
        )
        if (
            not normalized_request
            or _SHA256_RE.fullmatch(expected) is None
            or not isinstance(source_target_binding, PlayerSaveTargetBinding)
            or not isinstance(
                destination_target_binding,
                PlayerSaveTargetBinding,
            )
            or source_target_binding == destination_target_binding
        ):
            raise BattleIdentityContinuityError(
                "emulator_handoff_guard_context_invalid"
            )
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return None
            record = _record_from_payload(payload)
            if record.fingerprint != expected:
                raise BattleIdentityContinuityError(
                    "emulator_handoff_active_identity_changed"
                )
            existing_guard = record.emulator_handoff_guard
            if existing_guard is not None:
                if (
                    existing_guard.status == "armed"
                    and existing_guard.request_id == normalized_request
                    and existing_guard.source_target_binding_fingerprint
                    == source_target_binding.fingerprint
                    and existing_guard.destination_target_binding_fingerprint
                    == destination_target_binding.fingerprint
                ):
                    return existing_guard
                raise BattleIdentityContinuityError(
                    existing_guard.failure_reason
                    or "emulator_handoff_guard_already_pending"
                )
            checkpoint = record.progress_checkpoint
            revision_floor = (
                checkpoint.max_save_revision
                if checkpoint is not None
                else record.terminal_continuity.save_revision
                if record.terminal_continuity is not None
                else None
            )
            wave_floor = (
                checkpoint.max_current_wave
                if checkpoint is not None
                else None
            )
            if normalized_wave is not None:
                wave_floor = max(wave_floor or 0, normalized_wave)
            if revision_floor is None and wave_floor is None:
                raise BattleIdentityContinuityError(
                    "emulator_handoff_source_checkpoint_unavailable"
                )
            guard = ActiveBattleEmulatorHandoffGuard(
                request_id=normalized_request,
                identity_fingerprint=expected,
                source_target_binding_fingerprint=(
                    source_target_binding.fingerprint
                ),
                destination_target_binding_fingerprint=(
                    destination_target_binding.fingerprint
                ),
                source_save_revision=revision_floor,
                source_wave=wave_floor,
                armed_at=_aware_timestamp(armed_at).isoformat(),
            )
            payload["emulator_handoff_guard"] = guard.as_dict()
            self._write_payload(payload)
            return guard

    def cancel_emulator_handoff_guard(
        self,
        *,
        request_id: str,
        destination_target_binding: PlayerSaveTargetBinding,
    ) -> bool:
        """Remove only an unconsumed guard whose target move did not occur."""

        normalized_request = " ".join(str(request_id or "").split())[:128]
        if (
            not normalized_request
            or not isinstance(
                destination_target_binding,
                PlayerSaveTargetBinding,
            )
        ):
            return False
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            guard = record.emulator_handoff_guard
            if not (
                guard is not None
                and guard.status == "armed"
                and guard.request_id == normalized_request
                and guard.destination_target_binding_fingerprint
                == destination_target_binding.fingerprint
            ):
                return False
            payload.pop("emulator_handoff_guard", None)
            self._write_payload(payload)
            return True

    def _verify_emulator_handoff_guard(
        self,
        payload: dict[str, Any],
        guard: ActiveBattleEmulatorHandoffGuard,
        *,
        identity: ActiveRoundIdentity,
        acquisition: PlayerSaveAcquisitionBundle,
    ) -> None:
        """Consume a guard after exact destination same-battle proof."""

        if guard.status == "blocked":
            raise BattleIdentityContinuityError(
                guard.failure_reason
                or "emulator_handoff_save_rollback_detected"
            )
        observed_revision, observed_wave = _progress_values_from_acquisition(
            acquisition,
            identity_fingerprint=identity.fingerprint,
        )
        reason = ""
        if identity.fingerprint != guard.identity_fingerprint:
            reason = "emulator_handoff_active_identity_changed"
        elif (
            acquisition.binding_fingerprint
            != guard.destination_target_binding_fingerprint
        ):
            reason = "emulator_handoff_destination_changed"
        elif (
            guard.source_save_revision is not None
            and observed_revision is None
        ):
            reason = "emulator_handoff_save_revision_unavailable"
        elif (
            guard.source_save_revision is not None
            and observed_revision is not None
            and observed_revision < guard.source_save_revision
        ):
            reason = "emulator_handoff_save_revision_regressed"
        elif guard.source_wave is not None and observed_wave is None:
            reason = "emulator_handoff_current_wave_unavailable"
        # Wave rollback is expected when another PC opens the most recent
        # cloud save.  Exact ActiveRoundIdentity is the battle key; the source
        # and observed waves remain diagnostic progress evidence, not an
        # identity or action-authority boundary.
        if not reason:
            payload.pop("emulator_handoff_guard", None)
            return

        blocked = ActiveBattleEmulatorHandoffGuard(
            request_id=guard.request_id,
            identity_fingerprint=guard.identity_fingerprint,
            source_target_binding_fingerprint=(
                guard.source_target_binding_fingerprint
            ),
            destination_target_binding_fingerprint=(
                guard.destination_target_binding_fingerprint
            ),
            source_save_revision=guard.source_save_revision,
            source_wave=guard.source_wave,
            armed_at=guard.armed_at,
            status="blocked",
            failure_reason=reason,
            observed_save_revision=observed_revision,
            observed_wave=observed_wave,
            detected_at=datetime.now(timezone.utc).isoformat(),
        )
        payload["emulator_handoff_guard"] = blocked.as_dict()
        try:
            self._write_payload(payload)
        except BattleIdentityStoreError as exc:
            raise BattleIdentityContinuityError(
                "emulator_handoff_failure_persistence_failed"
            ) from exc
        raise BattleIdentityContinuityError(reason)

    def mark_inactive(
        self,
        *,
        reason: str,
        operation_id: str,
        acquisition: PlayerSaveAcquisitionBundle,
        observed_at: Optional[datetime] = None,
    ) -> None:
        """Persist a forced-save proof that no active round exists."""

        normalized_reason = " ".join(str(reason or "").split())[:256]
        normalized_operation = str(operation_id or "").strip()[:128]
        if not normalized_reason or not normalized_operation:
            raise BattleIdentityStoreError(
                "inactive battle reason and operation ID are required"
            )
        if not (
            isinstance(acquisition, PlayerSaveAcquisitionBundle)
            and acquisition.complete
            and acquisition.acquisition_type
            is PlayerSaveAcquisitionType.FORCED_SERIALIZATION
        ):
            raise BattleIdentityStoreError(
                "inactive battle proof requires forced serialization"
            )
        runtime = getattr(acquisition.snapshot, "runtime_save", None)
        if (
            getattr(runtime, "round_active", None) is not False
            or getattr(runtime, "active_round_identity", None) is not None
        ):
            raise BattleIdentityStoreError(
                "forced serialization does not prove an inactive round"
            )
        with self._lock:
            try:
                previous_payload = self._read_payload()
            except BattleIdentityStoreError:
                previous_payload = None
            previous_identity = None
            if (
                previous_payload is not None
                and previous_payload.get("status") == "active"
            ):
                previous_identity = _record_from_payload(
                    previous_payload
                ).identity.as_dict()
            payload: dict[str, Any] = {
                "schema_version": BATTLE_IDENTITY_SCHEMA_VERSION,
                "status": "inactive",
                "observed_at": _aware_timestamp(observed_at).isoformat(),
                "reason": normalized_reason,
                "operation_id": normalized_operation,
                "acquisition": acquisition.redacted_provenance(),
            }
            if previous_identity is not None:
                payload["previous_active_identity"] = previous_identity
            self._write_payload(payload)

    def record_session_preflight(
        self,
        *,
        identity_fingerprint: str,
        strategy: str,
        configuration_fingerprint: str,
        evidence: Mapping[str, Any],
        completed_at: Optional[datetime] = None,
    ) -> bool:
        """Attach restart-reusable validation only to the exact battle ID."""

        expected = str(identity_fingerprint or "").strip()
        normalized_strategy = str(strategy or "").strip()
        normalized_configuration = str(
            configuration_fingerprint or ""
        ).strip()
        if (
            _SHA256_RE.fullmatch(expected) is None
            or not normalized_strategy
            or _SHA256_RE.fullmatch(normalized_configuration) is None
            or not isinstance(evidence, Mapping)
        ):
            return False
        try:
            detached_evidence = json.loads(
                json.dumps(
                    dict(evidence),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except (OverflowError, RecursionError, TypeError, ValueError):
            return False
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            if record.fingerprint != expected:
                return False
            payload["session_preflight"] = {
                "schema_version": 1,
                "identity_fingerprint": expected,
                "strategy": normalized_strategy,
                "configuration_fingerprint": normalized_configuration,
                "completed_at": _aware_timestamp(completed_at).isoformat(),
                "evidence": detached_evidence,
            }
            self._write_payload(payload)
            return True

    def record_strategy_snapshot(
        self,
        *,
        identity_fingerprint: str,
        strategy: str,
        strategy_definition_fingerprint: str,
        session_preflight_configuration_fingerprint: str,
        run_configuration: Mapping[str, Any],
        recorded_at: Optional[datetime] = None,
    ) -> bool:
        """Retain one immutable reporting snapshot for the exact battle."""

        expected = str(identity_fingerprint or "").strip()
        strategy_name = str(strategy or "").strip()
        definition_fingerprint = str(
            strategy_definition_fingerprint or ""
        ).strip()
        preflight_fingerprint = str(
            session_preflight_configuration_fingerprint or ""
        ).strip()
        if (
            _SHA256_RE.fullmatch(expected) is None
            or not strategy_name
            or _SHA256_RE.fullmatch(definition_fingerprint) is None
            or _SHA256_RE.fullmatch(preflight_fingerprint) is None
            or not isinstance(run_configuration, Mapping)
        ):
            return False
        try:
            detached_configuration = _detached_json_mapping(run_configuration)
        except (OverflowError, RecursionError, TypeError, ValueError):
            return False
        material = _strategy_snapshot_material(
            identity_fingerprint=expected,
            strategy=strategy_name,
            strategy_definition_fingerprint=definition_fingerprint,
            session_preflight_configuration_fingerprint=(
                preflight_fingerprint
            ),
            run_configuration=detached_configuration,
            recorded_at=_aware_timestamp(recorded_at),
            provenance={
                "schema_version": 1,
                "kind": "settled_active_battle_observation",
            },
        )
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            if record.fingerprint != expected:
                return False
            existing = record.strategy_snapshot
            if existing is not None:
                # Strategy identity is immutable for this battle. A changed
                # candidate is selected for a later safe boundary instead;
                # an identical repeat is simply already satisfied.
                return False
            payload["strategy_snapshot"] = material
            self._write_payload(payload)
            return True

    def record_operator_terminal_strategy_attestation(
        self,
        *,
        identity_fingerprint: str,
        strategy: str,
        strategy_definition_fingerprint: str,
        session_preflight_configuration_fingerprint: str,
        run_configuration: Mapping[str, Any],
        runtime_id: str,
        pid: int,
        target_binding: PlayerSaveTargetBinding,
        observation_id: str,
        observed_at: datetime,
        reason: str,
        attested_at: Optional[datetime] = None,
    ) -> bool:
        """Atomically attest one legacy terminal and its unchanged Strategy.

        This is an explicit trusted-operator exception for a retained battle
        that predates the automatic durable snapshot. It never supplies action
        authority, and it requires the older exact-battle preflight receipt to
        match the Strategy definition currently being attested.
        """

        expected = str(identity_fingerprint or "").strip()
        strategy_name = str(strategy or "").strip()
        definition_fingerprint = str(
            strategy_definition_fingerprint or ""
        ).strip()
        preflight_fingerprint = str(
            session_preflight_configuration_fingerprint or ""
        ).strip()
        normalized_runtime = str(runtime_id or "").strip()[:128]
        normalized_observation = str(observation_id or "").strip()[:160]
        normalized_reason = " ".join(str(reason or "").split())[:256]
        if (
            _SHA256_RE.fullmatch(expected) is None
            or not strategy_name
            or _SHA256_RE.fullmatch(definition_fingerprint) is None
            or _SHA256_RE.fullmatch(preflight_fingerprint) is None
            or not isinstance(run_configuration, Mapping)
            or not normalized_runtime
            or type(pid) is not int
            or pid <= 0
            or not isinstance(target_binding, PlayerSaveTargetBinding)
            or not normalized_observation
            or not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or not normalized_reason
        ):
            return False
        try:
            detached_configuration = _detached_json_mapping(run_configuration)
        except (OverflowError, RecursionError, TypeError, ValueError):
            return False
        timestamp = _aware_timestamp(attested_at)
        attestation_id = _json_fingerprint(
            {
                "identity_fingerprint": expected,
                "strategy": strategy_name,
                "runtime_id": normalized_runtime,
                "pid": pid,
                "target_binding_fingerprint": target_binding.fingerprint,
                "observation_id": normalized_observation,
                "observed_at": observed_at.isoformat(),
                "attested_at": timestamp.isoformat(),
            }
        )
        operator_material = _strategy_snapshot_material(
            identity_fingerprint=expected,
            strategy=strategy_name,
            strategy_definition_fingerprint=definition_fingerprint,
            session_preflight_configuration_fingerprint=(
                preflight_fingerprint
            ),
            run_configuration=detached_configuration,
            recorded_at=timestamp,
            provenance={
                "schema_version": 1,
                "kind": "operator_terminal_attestation",
                "attestation_id": attestation_id,
            },
        )
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            if (
                record.fingerprint != expected
                or record.operator_terminal_attestation is not None
            ):
                return False
            receipt = _validated_session_preflight(
                record.session_preflight,
                identity_fingerprint=expected,
            )
            evidence = (
                receipt.get("evidence")
                if isinstance(receipt, Mapping)
                else None
            )
            if not (
                isinstance(receipt, Mapping)
                and receipt.get("strategy") == strategy_name
                and receipt.get("configuration_fingerprint")
                == preflight_fingerprint
                and isinstance(evidence, Mapping)
                and evidence.get("valid") is True
                and isinstance(evidence.get("failed_checks"), list)
                and not evidence["failed_checks"]
            ):
                return False
            existing_snapshot = record.strategy_snapshot
            if existing_snapshot is not None:
                if not (
                    existing_snapshot.get("strategy") == strategy_name
                    and existing_snapshot.get(
                        "strategy_definition_fingerprint"
                    )
                    == definition_fingerprint
                    and existing_snapshot.get(
                        "session_preflight_configuration_fingerprint"
                    )
                    == preflight_fingerprint
                    and existing_snapshot.get(
                        "run_configuration_fingerprint"
                    )
                    == _json_fingerprint(detached_configuration)
                ):
                    return False
                material = dict(existing_snapshot)
                snapshot_source = "independently_durable"
            else:
                material = operator_material
                snapshot_source = "operator_backfill"
            attestation = {
                "schema_version": 1,
                "attestation_id": attestation_id,
                "identity_fingerprint": expected,
                "statement": "terminal_and_strategy_unchanged_since_battle",
                "reason": normalized_reason,
                "attested_at": timestamp.isoformat(),
                "runtime": {
                    "runtime_id": normalized_runtime,
                    "pid": pid,
                    "adb_target": target_binding.target,
                    "target_generation": target_binding.generation,
                    "target_binding_fingerprint": target_binding.fingerprint,
                },
                "observation": {
                    "observation_id": normalized_observation,
                    "observed_at": observed_at.isoformat(),
                    "primary_state": "GAME_OVER",
                    "game_state": "game_over",
                },
                "strategy_snapshot_source": snapshot_source,
                "strategy_snapshot_fingerprint": material["fingerprint"],
            }
            attestation["fingerprint"] = _mapping_fingerprint(attestation)
            payload["strategy_snapshot"] = material
            payload["operator_terminal_attestation"] = attestation
            self._write_payload(payload)
            return True

    def record_survival_activation_checkpoint(
        self,
        *,
        identity_fingerprint: str,
        tracker_configuration_fingerprint: str,
        checkpoint: Mapping[str, Any],
        recorded_at: Optional[datetime] = None,
    ) -> bool:
        """Retain one validated tracker checkpoint under its exact battle."""

        expected = str(identity_fingerprint or "").strip()
        configuration_fingerprint = str(
            tracker_configuration_fingerprint or ""
        ).strip()
        if (
            _SHA256_RE.fullmatch(expected) is None
            or _SHA256_RE.fullmatch(configuration_fingerprint) is None
            or battle_activation_checkpoint_configuration_fingerprint(
                checkpoint,
                expected_identity_fingerprint=expected,
            )
            != configuration_fingerprint
        ):
            return False
        try:
            detached_checkpoint = _detached_json_mapping(checkpoint)
        except (OverflowError, RecursionError, TypeError, ValueError):
            return False
        envelope = {
            "schema_version": 1,
            "identity_fingerprint": expected,
            "tracker_configuration_fingerprint": configuration_fingerprint,
            "recorded_at": _aware_timestamp(recorded_at).isoformat(),
            "checkpoint": detached_checkpoint,
        }
        with self._lock:
            payload = self._read_payload()
            if payload is None or payload.get("status") != "active":
                return False
            record = _record_from_payload(payload)
            if record.fingerprint != expected:
                return False
            existing = record.survival_activation_checkpoint
            if isinstance(existing, Mapping):
                existing_checkpoint = existing.get("checkpoint")
                if isinstance(existing_checkpoint, Mapping):
                    old_last = existing_checkpoint.get("last_save")
                    new_last = detached_checkpoint.get("last_save")
                    old_revision = (
                        old_last.get("revision")
                        if isinstance(old_last, Mapping)
                        else None
                    )
                    new_revision = (
                        new_last.get("revision")
                        if isinstance(new_last, Mapping)
                        else None
                    )
                    if (
                        type(old_revision) is int
                        and (
                            type(new_revision) is not int
                            or new_revision < old_revision
                        )
                    ):
                        return False
                    old_wave = (
                        old_last.get("wave")
                        if isinstance(old_last, Mapping)
                        else None
                    )
                    new_wave = (
                        new_last.get("wave")
                        if isinstance(new_last, Mapping)
                        else None
                    )
                    if (
                        type(old_wave) is int
                        and (
                            type(new_wave) is not int
                            or new_wave < old_wave
                        )
                    ):
                        return False
                    if existing_checkpoint == detached_checkpoint:
                        return False
            payload["survival_activation_checkpoint"] = envelope
            self._write_payload(payload)
            return True

    def _read_payload(self) -> Optional[dict[str, Any]]:
        payload = _read_json(self.path)
        if payload is None:
            return None
        if payload.get("schema_version") != BATTLE_IDENTITY_SCHEMA_VERSION:
            raise BattleIdentityStoreError(
                "unsupported battle identity schema"
            )
        status = str(payload.get("status") or "").strip()
        if status not in {"active", "inactive"}:
            raise BattleIdentityStoreError(
                "battle identity status is malformed"
            )
        if status == "active":
            _record_from_payload(payload)
        return payload

    def _write_payload(self, payload: Mapping[str, Any]) -> None:
        try:
            _write_json_atomic(self.path, payload)
        except OSError as exc:
            raise BattleIdentityStoreError(
                "battle identity record could not be written"
            ) from exc


class ActiveBattleIdentityCoordinator:
    """Force one running save and bind its canonical active-round identity."""

    def __init__(
        self,
        *,
        acquirer: StablePlayerSaveAcquirer,
        store: BattleIdentityStore,
        context_fn: Callable[[], Optional[BattleIdentityCheckContext]],
        source_guard_fn: Callable[[Any, bool], bool],
        background_fn: Optional[Callable[[str], Any]] = None,
        foreground_fn: Optional[Callable[[str], Any]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not isinstance(acquirer, StablePlayerSaveAcquirer):
            raise TypeError("battle identity requires the shared acquirer")
        if not isinstance(store, BattleIdentityStore):
            raise TypeError("battle identity requires its durable store")
        self._acquirer = acquirer
        self._store = store
        self._context_fn = context_fn
        self._source_guard_fn = source_guard_fn
        self._background_fn = background_fn
        self._foreground_fn = foreground_fn
        self._sleep_fn = sleep_fn

    def bind(
        self,
        *,
        context: BattleIdentityCheckContext,
        action_guard_fn: Callable[[], bool],
        reason: str,
        initial_frame: Any = None,
        expected_identity_fingerprint: Optional[str] = None,
        source_label: str = "the running battle",
        source_guard_fn: Optional[Callable[[Any, bool], bool]] = None,
        stable_initial_source: bool = False,
    ) -> BattleIdentityCheckResult:
        """Force serialization, then adopt or verify the decoded identity."""

        if not isinstance(context, BattleIdentityCheckContext):
            raise TypeError("battle identity check requires a typed context")
        serializer_kwargs: dict[str, Any] = {
            "acquirer": self._acquirer,
            "context_guard_fn": lambda: self._context_matches(context),
            "action_guard_fn": action_guard_fn,
            "source_guard_fn": source_guard_fn or self._source_guard_fn,
            "log_prefix": "BATTLE_IDENTITY",
        }
        if self._background_fn is not None:
            serializer_kwargs["background_fn"] = self._background_fn
        if self._foreground_fn is not None:
            serializer_kwargs["foreground_fn"] = self._foreground_fn
        if self._sleep_fn is not None:
            serializer_kwargs["sleep_fn"] = self._sleep_fn
        serialized = GuardedPlayerSaveSerializer(**serializer_kwargs).acquire(
            expected_target=context.target_binding.target,
            expected_generation=context.target_binding.generation,
            target_generation_detail=context.target_binding.fingerprint[:16],
            source_label=str(source_label or "the battle boundary"),
            initial_frame=initial_frame,
            stable_initial_source=bool(stable_initial_source),
        )
        if serialized.status is GuardedSerializationStatus.BLOCKED:
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.BLOCKED,
                serialized.reason,
                acquisition=serialized.acquisition,
                source_restored=serialized.source_restored,
                lifecycle_input_attempted=(
                    serialized.lifecycle_input_attempted
                ),
            )
        acquisition = serialized.acquisition
        snapshot = serialized.snapshot
        if snapshot is None or acquisition is None:
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.UNAVAILABLE,
                serialized.reason,
                acquisition=acquisition,
                source_restored=serialized.source_restored,
                lifecycle_input_attempted=(
                    serialized.lifecycle_input_attempted
                ),
            )
        runtime = getattr(snapshot, "runtime_save", None)
        identity = getattr(runtime, "active_round_identity", None)
        if (
            getattr(runtime, "round_active", None) is not True
            or not isinstance(identity, ActiveRoundIdentity)
        ):
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.UNAVAILABLE,
                "active_round_identity_unavailable_after_forced_serialization",
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        if _validated_identity(identity.as_dict()) is None:
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.UNAVAILABLE,
                "active_round_identity_invalid_after_forced_serialization",
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        expected = str(expected_identity_fingerprint or "").strip()
        if expected and identity.fingerprint != expected:
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.BLOCKED,
                "active_round_identity_changed",
                identity=identity,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        if not self._context_matches(context):
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.BLOCKED,
                "battle_identity_operation_changed_after_restore",
                identity=identity,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        try:
            _record, relation = self._store.bind(
                identity,
                reason=reason,
                operation_id=context.operation_id,
                acquisition=acquisition,
                bound_at=acquisition.captured_at,
            )
        except BattleIdentityContinuityError as exc:
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.BLOCKED,
                exc.reason,
                identity=identity,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        except (BattleIdentityStoreError, OSError):
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.UNAVAILABLE,
                "battle_identity_persistence_failed",
                identity=identity,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )
        return BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=relation,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )

    def _context_matches(self, expected: BattleIdentityCheckContext) -> bool:
        try:
            return self._context_fn() == expected
        except Exception:
            return False


def _validated_identity(value: object) -> Optional[ActiveRoundIdentity]:
    if not isinstance(value, Mapping):
        return None
    raw_values = {
        "game_version": value.get("game_version"),
        "current_tier": value.get("current_tier"),
        "rounds_started_this_tier": value.get("rounds_started_this_tier"),
        "round_seed": value.get("round_seed"),
    }
    if any(type(item) is not int for item in raw_values.values()):
        return None
    if (
        raw_values["game_version"] <= 0
        or raw_values["current_tier"] < 0
        or raw_values["rounds_started_this_tier"] < 0
        or raw_values["round_seed"] <= 0
    ):
        return None
    rendered = json.dumps(
        raw_values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(rendered).hexdigest()
    if str(value.get("fingerprint") or "") != fingerprint:
        return None
    return ActiveRoundIdentity(
        game_version=int(raw_values["game_version"]),
        current_tier=int(raw_values["current_tier"]),
        rounds_started_this_tier=int(
            raw_values["rounds_started_this_tier"]
        ),
        round_seed=int(raw_values["round_seed"]),
        fingerprint=fingerprint,
    )


def _record_from_payload(payload: Mapping[str, Any]) -> ActiveBattleIdentityRecord:
    if payload.get("schema_version") != BATTLE_IDENTITY_SCHEMA_VERSION:
        raise BattleIdentityStoreError("unsupported battle identity schema")
    identity = _validated_identity(payload.get("identity"))
    bound_at = str(payload.get("bound_at") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    operation_id = str(payload.get("operation_id") or "").strip()
    try:
        parsed = datetime.fromisoformat(bound_at)
    except ValueError as exc:
        raise BattleIdentityStoreError(
            "battle identity timestamp is invalid"
        ) from exc
    acquisition = payload.get("acquisition")
    session_preflight = _validated_session_preflight(
        payload.get("session_preflight"),
        identity_fingerprint=(identity.fingerprint if identity else ""),
    )
    strategy_snapshot = _validated_strategy_snapshot(
        payload.get("strategy_snapshot"),
        identity_fingerprint=(identity.fingerprint if identity else ""),
    )
    operator_terminal_attestation = _validated_operator_terminal_attestation(
        payload.get("operator_terminal_attestation"),
        identity_fingerprint=(identity.fingerprint if identity else ""),
        strategy_snapshot=strategy_snapshot,
    )
    snapshot_provenance = (
        strategy_snapshot.get("provenance")
        if isinstance(strategy_snapshot, Mapping)
        else None
    )
    if (
        isinstance(snapshot_provenance, Mapping)
        and snapshot_provenance.get("kind")
        == "operator_terminal_attestation"
        and operator_terminal_attestation is None
    ):
        strategy_snapshot = None
    survival_activation_checkpoint = (
        _validated_survival_activation_checkpoint(
            payload.get("survival_activation_checkpoint"),
            identity_fingerprint=(identity.fingerprint if identity else ""),
        )
    )
    terminal_continuity_value = payload.get("terminal_continuity")
    progress_checkpoint_value = payload.get("progress_checkpoint")
    emulator_handoff_guard_value = payload.get("emulator_handoff_guard")
    if (
        identity is None
        or parsed.tzinfo is None
        or not reason
        or not operation_id
        or not isinstance(acquisition, Mapping)
    ):
        raise BattleIdentityStoreError("battle identity record is malformed")
    terminal_continuity = _validated_terminal_continuity(
        terminal_continuity_value,
        acquisition=acquisition,
    )
    if terminal_continuity_value is not None and terminal_continuity is None:
        raise BattleIdentityStoreError(
            "battle terminal continuity record is malformed"
        )
    progress_checkpoint = _validated_progress_checkpoint(
        progress_checkpoint_value
    )
    if progress_checkpoint_value is not None and progress_checkpoint is None:
        raise BattleIdentityStoreError(
            "battle progress checkpoint is malformed"
        )
    emulator_handoff_guard = _validated_emulator_handoff_guard(
        emulator_handoff_guard_value,
        identity_fingerprint=identity.fingerprint,
    )
    if (
        emulator_handoff_guard_value is not None
        and emulator_handoff_guard is None
    ):
        raise BattleIdentityStoreError(
            "emulator handoff guard is malformed"
        )
    return ActiveBattleIdentityRecord(
        identity=identity,
        bound_at=bound_at,
        reason=reason,
        operation_id=operation_id,
        acquisition=dict(acquisition),
        session_preflight=(
            dict(session_preflight)
            if isinstance(session_preflight, Mapping)
            else None
        ),
        strategy_snapshot=(
            dict(strategy_snapshot)
            if isinstance(strategy_snapshot, Mapping)
            else None
        ),
        operator_terminal_attestation=(
            dict(operator_terminal_attestation)
            if isinstance(operator_terminal_attestation, Mapping)
            else None
        ),
        survival_activation_checkpoint=(
            dict(survival_activation_checkpoint)
            if isinstance(survival_activation_checkpoint, Mapping)
            else None
        ),
        terminal_continuity=terminal_continuity,
        progress_checkpoint=progress_checkpoint,
        emulator_handoff_guard=emulator_handoff_guard,
    )


def _progress_values_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    identity_fingerprint: str,
) -> tuple[Optional[int], Optional[int]]:
    if not (
        isinstance(acquisition, PlayerSaveAcquisitionBundle)
        and acquisition.complete
        and _SHA256_RE.fullmatch(identity_fingerprint) is not None
    ):
        return None, None
    snapshot = acquisition.snapshot
    runtime = getattr(snapshot, "runtime_save", None)
    identity = getattr(runtime, "active_round_identity", None)
    if not (
        getattr(runtime, "round_active", None) is True
        and isinstance(identity, ActiveRoundIdentity)
        and identity.fingerprint == identity_fingerprint
    ):
        return None, None
    runtime_revision = getattr(runtime, "save_revision", None)
    snapshot_revision = getattr(snapshot, "save_revision", None)
    save_revision = (
        runtime_revision
        if (
            getattr(runtime, "save_revision_status", None) == "observed"
            and getattr(runtime, "save_revision_reason", None) == ""
            and type(runtime_revision) is int
            and runtime_revision >= 0
            and snapshot_revision == runtime_revision
        )
        else None
    )
    runtime_wave = getattr(runtime, "current_wave", None)
    current_wave = (
        runtime_wave
        if (
            getattr(runtime, "current_wave_status", None) == "observed"
            and getattr(runtime, "current_wave_reason", None) == ""
            and type(runtime_wave) is int
            and runtime_wave >= 0
        )
        else None
    )
    return save_revision, current_wave


def _progress_checkpoint_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    identity_fingerprint: str,
    observed_at: Optional[datetime] = None,
) -> Optional[ActiveBattleProgressCheckpoint]:
    save_revision, current_wave = _progress_values_from_acquisition(
        acquisition,
        identity_fingerprint=identity_fingerprint,
    )
    binding_fingerprint = acquisition.binding_fingerprint
    if not (
        save_revision is not None
        and current_wave is not None
        and _SHA256_RE.fullmatch(str(binding_fingerprint or "")) is not None
    ):
        return None
    timestamp = observed_at or acquisition.captured_at
    return ActiveBattleProgressCheckpoint(
        max_save_revision=save_revision,
        max_current_wave=current_wave,
        updated_at=_aware_timestamp(timestamp).isoformat(),
        target_binding_fingerprint=str(binding_fingerprint),
    )


def _merge_progress_checkpoints(
    previous: Optional[ActiveBattleProgressCheckpoint],
    current: Optional[ActiveBattleProgressCheckpoint],
) -> Optional[ActiveBattleProgressCheckpoint]:
    if previous is None:
        return current
    if current is None:
        return previous
    max_revision = max(
        previous.max_save_revision,
        current.max_save_revision,
    )
    max_wave = max(previous.max_current_wave, current.max_current_wave)
    if (
        max_revision == previous.max_save_revision
        and max_wave == previous.max_current_wave
    ):
        return previous
    return ActiveBattleProgressCheckpoint(
        max_save_revision=max_revision,
        max_current_wave=max_wave,
        updated_at=current.updated_at,
        target_binding_fingerprint=current.target_binding_fingerprint,
    )


def _validated_progress_checkpoint(
    value: object,
) -> Optional[ActiveBattleProgressCheckpoint]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    try:
        return ActiveBattleProgressCheckpoint(
            max_save_revision=value.get("max_save_revision"),
            max_current_wave=value.get("max_current_wave"),
            updated_at=str(value.get("updated_at") or "").strip(),
            target_binding_fingerprint=str(
                value.get("target_binding_fingerprint") or ""
            ).strip(),
        )
    except (TypeError, ValueError):
        return None


def _validated_emulator_handoff_guard(
    value: object,
    *,
    identity_fingerprint: str,
) -> Optional[ActiveBattleEmulatorHandoffGuard]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    try:
        guard = ActiveBattleEmulatorHandoffGuard(
            request_id=str(value.get("request_id") or "").strip(),
            identity_fingerprint=str(
                value.get("identity_fingerprint") or ""
            ).strip(),
            source_target_binding_fingerprint=str(
                value.get("source_target_binding_fingerprint") or ""
            ).strip(),
            destination_target_binding_fingerprint=str(
                value.get("destination_target_binding_fingerprint") or ""
            ).strip(),
            source_save_revision=value.get("source_save_revision"),
            source_wave=value.get("source_wave"),
            armed_at=str(value.get("armed_at") or "").strip(),
            status=str(value.get("status") or "").strip(),
            failure_reason=(
                str(value.get("failure_reason") or "").strip() or None
            ),
            observed_save_revision=value.get("observed_save_revision"),
            observed_wave=value.get("observed_wave"),
            detected_at=(
                str(value.get("detected_at") or "").strip() or None
            ),
        )
    except (TypeError, ValueError):
        return None
    if guard.identity_fingerprint != identity_fingerprint:
        return None
    return guard


def _validated_session_preflight(
    value: object,
    *,
    identity_fingerprint: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    completed_at = str(value.get("completed_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    evidence = value.get("evidence")
    if (
        parsed.tzinfo is None
        or value.get("identity_fingerprint") != identity_fingerprint
        or not str(value.get("strategy") or "").strip()
        or _SHA256_RE.fullmatch(
            str(value.get("configuration_fingerprint") or "")
        )
        is None
        or not isinstance(evidence, Mapping)
    ):
        return None
    try:
        detached = _detached_json_mapping(value)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None
    return detached


def _validated_strategy_snapshot(
    value: object,
    *,
    identity_fingerprint: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    recorded_at = str(value.get("recorded_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(recorded_at)
    except ValueError:
        return None
    provenance = value.get("provenance")
    if provenance is not None and not (
        isinstance(provenance, Mapping)
        and provenance.get("schema_version") == 1
        and provenance.get("kind")
        in {
            "settled_active_battle_observation",
            "operator_terminal_attestation",
        }
        and (
            provenance.get("kind") != "operator_terminal_attestation"
            or _SHA256_RE.fullmatch(
                str(provenance.get("attestation_id") or "")
            )
            is not None
        )
    ):
        return None
    if (
        parsed.tzinfo is None
        or value.get("identity_fingerprint") != identity_fingerprint
        or not str(value.get("strategy") or "").strip()
        or _SHA256_RE.fullmatch(
            str(value.get("strategy_definition_fingerprint") or "")
        )
        is None
        or _SHA256_RE.fullmatch(
            str(
                value.get(
                    "session_preflight_configuration_fingerprint"
                )
                or ""
            )
        )
        is None
        or not isinstance(value.get("run_configuration"), Mapping)
        or _SHA256_RE.fullmatch(
            str(value.get("run_configuration_fingerprint") or "")
        )
        is None
        or _json_fingerprint(value.get("run_configuration"))
        != value.get("run_configuration_fingerprint")
        or _SHA256_RE.fullmatch(str(value.get("fingerprint") or "")) is None
        or _mapping_fingerprint(value) != value.get("fingerprint")
    ):
        return None
    try:
        return _detached_json_mapping(value)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def _validated_operator_terminal_attestation(
    value: object,
    *,
    identity_fingerprint: str,
    strategy_snapshot: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    attested_at = str(value.get("attested_at") or "").strip()
    runtime = value.get("runtime")
    observation = value.get("observation")
    try:
        parsed_attestation = datetime.fromisoformat(attested_at)
        parsed_observation = datetime.fromisoformat(
            str(
                observation.get("observed_at")
                if isinstance(observation, Mapping)
                else ""
            )
        )
        target_binding = PlayerSaveTargetBinding(
            str(
                runtime.get("adb_target")
                if isinstance(runtime, Mapping)
                else ""
            ),
            int(
                runtime.get("target_generation")
                if isinstance(runtime, Mapping)
                else 0
            ),
        )
    except (TypeError, ValueError):
        return None
    provenance = (
        strategy_snapshot.get("provenance")
        if isinstance(strategy_snapshot, Mapping)
        else None
    )
    snapshot_source = value.get("strategy_snapshot_source")
    operator_backfill = bool(
        snapshot_source == "operator_backfill"
        and isinstance(provenance, Mapping)
        and provenance.get("kind") == "operator_terminal_attestation"
        and provenance.get("attestation_id") == value.get("attestation_id")
    )
    independently_durable = bool(
        snapshot_source == "independently_durable"
        and (
            provenance is None
            or (
                isinstance(provenance, Mapping)
                and provenance.get("kind")
                == "settled_active_battle_observation"
            )
        )
    )
    if (
        parsed_attestation.tzinfo is None
        or parsed_observation.tzinfo is None
        or value.get("identity_fingerprint") != identity_fingerprint
        or value.get("statement")
        != "terminal_and_strategy_unchanged_since_battle"
        or not str(value.get("reason") or "").strip()
        or _SHA256_RE.fullmatch(str(value.get("attestation_id") or ""))
        is None
        or not isinstance(runtime, Mapping)
        or not str(runtime.get("runtime_id") or "").strip()
        or type(runtime.get("pid")) is not int
        or runtime["pid"] <= 0
        or runtime.get("target_binding_fingerprint")
        != target_binding.fingerprint
        or not isinstance(observation, Mapping)
        or not str(observation.get("observation_id") or "").strip()
        or observation.get("primary_state") != "GAME_OVER"
        or observation.get("game_state") != "game_over"
        or not isinstance(strategy_snapshot, Mapping)
        or value.get("strategy_snapshot_fingerprint")
        != strategy_snapshot.get("fingerprint")
        or not (operator_backfill or independently_durable)
        or _SHA256_RE.fullmatch(str(value.get("fingerprint") or "")) is None
        or _mapping_fingerprint(value) != value.get("fingerprint")
    ):
        return None
    try:
        return _detached_json_mapping(value)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def _validated_survival_activation_checkpoint(
    value: object,
    *,
    identity_fingerprint: str,
) -> Optional[dict[str, Any]]:
    if not (
        isinstance(value, Mapping)
        and value.get("schema_version") == 1
        and value.get("identity_fingerprint") == identity_fingerprint
        and isinstance(value.get("checkpoint"), Mapping)
    ):
        return None
    configuration_fingerprint = str(
        value.get("tracker_configuration_fingerprint") or ""
    ).strip()
    recorded_at = str(value.get("recorded_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(recorded_at)
    except ValueError:
        return None
    if (
        parsed.tzinfo is None
        or _SHA256_RE.fullmatch(configuration_fingerprint) is None
        or battle_activation_checkpoint_configuration_fingerprint(
            value.get("checkpoint"),
            expected_identity_fingerprint=identity_fingerprint,
        )
        != configuration_fingerprint
    ):
        return None
    try:
        return _detached_json_mapping(value)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def _terminal_continuity_from_acquisition(
    acquisition: PlayerSaveAcquisitionBundle,
) -> Optional[ActiveBattleTerminalContinuity]:
    snapshot = acquisition.snapshot
    runtime = getattr(snapshot, "runtime_save", None)
    vector = getattr(runtime, "round_counter_vector", None)
    runtime_revision = getattr(runtime, "save_revision", None)
    snapshot_revision = getattr(snapshot, "save_revision", None)
    target_fingerprint = acquisition.binding_fingerprint
    if not (
        getattr(runtime, "round_counter_vector_status", None) == "observed"
        and getattr(runtime, "round_counter_vector_reason", None) == ""
        and isinstance(vector, RoundCounterVectorEvidence)
        and type(vector.tier_count) is int
        and vector.tier_count > 0
        and _SHA256_RE.fullmatch(vector.fingerprint) is not None
        and getattr(runtime, "save_revision_status", None) == "observed"
        and getattr(runtime, "save_revision_reason", None) == ""
        and type(runtime_revision) is int
        and runtime_revision >= 0
        and snapshot_revision == runtime_revision
        and _SHA256_RE.fullmatch(str(target_fingerprint or "")) is not None
    ):
        return None
    return ActiveBattleTerminalContinuity(
        round_counter_vector_fingerprint=vector.fingerprint,
        round_counter_tier_count=vector.tier_count,
        save_revision=runtime_revision,
        target_binding_fingerprint=str(target_fingerprint),
    )


def _validated_terminal_continuity(
    value: object,
    *,
    acquisition: object,
) -> Optional[ActiveBattleTerminalContinuity]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return None
    vector_fingerprint = str(
        value.get("round_counter_vector_fingerprint") or ""
    ).strip()
    target_fingerprint = str(
        value.get("target_binding_fingerprint") or ""
    ).strip()
    tier_count = value.get("round_counter_tier_count")
    save_revision = value.get("save_revision")
    acquisition_binding = (
        acquisition.get("binding_fingerprint")
        if isinstance(acquisition, Mapping)
        else None
    )
    if not (
        _SHA256_RE.fullmatch(vector_fingerprint) is not None
        and _SHA256_RE.fullmatch(target_fingerprint) is not None
        and target_fingerprint == acquisition_binding
        and type(tier_count) is int
        and tier_count > 0
        and type(save_revision) is int
        and save_revision >= 0
    ):
        return None
    return ActiveBattleTerminalContinuity(
        round_counter_vector_fingerprint=vector_fingerprint,
        round_counter_tier_count=tier_count,
        save_revision=save_revision,
        target_binding_fingerprint=target_fingerprint,
    )


def durable_terminal_report_evidence_from_record(
    record: ActiveBattleIdentityRecord,
    *,
    terminal_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Restore components only after an accepted terminal binding proof."""

    if not isinstance(record, ActiveBattleIdentityRecord):
        return {}
    identity = str(record.fingerprint or "").strip()
    if not (
        isinstance(terminal_binding, Mapping)
        and terminal_binding.get("status") == "bound"
        and terminal_binding.get("binding_source")
        in {
            "durable_full_round_counter_vector",
            "operator_terminal_attestation",
        }
        and terminal_binding.get("active_round_identity_fingerprint")
        == identity
    ):
        return {}
    strategy_snapshot = _validated_strategy_snapshot(
        record.strategy_snapshot,
        identity_fingerprint=identity,
    )
    restored: dict[str, Any] = {}
    components: list[str] = []
    component_fingerprints: dict[str, str] = {}
    component_sources: dict[str, str] = {}
    receipt = _validated_session_preflight(
        record.session_preflight,
        identity_fingerprint=identity,
    )
    if strategy_snapshot is not None:
        strategy = str(strategy_snapshot["strategy"])
        restored.update(
            {
                "strategy": strategy,
                "run_configuration": _detached_json_mapping(
                    strategy_snapshot["run_configuration"]
                ),
                "strategy_definition_fingerprint": strategy_snapshot[
                    "strategy_definition_fingerprint"
                ],
            }
        )
        components.append("strategy_snapshot")
        component_fingerprints["strategy_snapshot"] = str(
            strategy_snapshot["fingerprint"]
        )
        provenance = strategy_snapshot.get("provenance")
        component_sources["strategy_snapshot"] = str(
            provenance.get("kind")
            if isinstance(provenance, Mapping)
            else "legacy_settled_active_battle_observation"
        )
        if (
            receipt is not None
            and receipt.get("strategy") == strategy
            and receipt.get("configuration_fingerprint")
            == strategy_snapshot.get(
                "session_preflight_configuration_fingerprint"
            )
        ):
            evidence = _detached_json_mapping(receipt["evidence"])
            if (
                evidence.get("valid") is True
                and isinstance(evidence.get("failed_checks"), list)
                and not evidence["failed_checks"]
            ):
                restored["session_preflight_evidence"] = evidence
                components.append("session_preflight_evidence")
                component_fingerprints["session_preflight_evidence"] = str(
                    receipt["configuration_fingerprint"]
                )
                component_sources["session_preflight_evidence"] = (
                    "exact_battle_session_preflight_receipt"
                )
    elif receipt is not None:
        evidence = _detached_json_mapping(receipt["evidence"])
        if (
            evidence.get("valid") is True
            and isinstance(evidence.get("failed_checks"), list)
            and not evidence["failed_checks"]
        ):
            restored["strategy"] = str(receipt["strategy"])
            restored["session_preflight_evidence"] = evidence
            components.append("session_preflight_evidence")
            component_fingerprints["session_preflight_evidence"] = str(
                receipt["configuration_fingerprint"]
            )
            component_sources["session_preflight_evidence"] = (
                "exact_battle_session_preflight_receipt"
            )

    envelope = _validated_survival_activation_checkpoint(
        record.survival_activation_checkpoint,
        identity_fingerprint=identity,
    )
    if envelope is not None:
        checkpoint = envelope["checkpoint"]
        activations = battle_activation_snapshot_from_checkpoint(
            checkpoint,
            expected_identity_fingerprint=identity,
        )
        if activations is not None:
            last_save = checkpoint.get("last_save")
            activations["durable_restoration"] = {
                "schema_version": 1,
                "status": "observed_events_through_checkpoint",
                "complete_history": False,
                "checkpoint_recorded_at": envelope["recorded_at"],
                "last_save_revision": (
                    last_save.get("revision")
                    if isinstance(last_save, Mapping)
                    else None
                ),
                "last_saved_wave": (
                    last_save.get("wave")
                    if isinstance(last_save, Mapping)
                    else None
                ),
            }
            restored["survival_ability_activations"] = activations
            components.append("survival_ability_activations")
            component_fingerprints["survival_ability_activations"] = str(
                envelope["tracker_configuration_fingerprint"]
            )
            component_sources["survival_ability_activations"] = (
                "exact_battle_activation_checkpoint"
            )

    if not components:
        return {}

    restored["durable_terminal_evidence"] = {
        "schema_version": 1,
        "status": "restored",
        "binding_source": str(terminal_binding["binding_source"]),
        "active_round_identity_fingerprint": identity,
        "components": components,
        "component_fingerprints": component_fingerprints,
        "component_sources": component_sources,
    }
    if terminal_binding.get("binding_source") == "operator_terminal_attestation":
        attestation = _validated_operator_terminal_attestation(
            record.operator_terminal_attestation,
            identity_fingerprint=identity,
            strategy_snapshot=strategy_snapshot,
        )
        if attestation is None or terminal_binding.get(
            "operator_attestation_fingerprint"
        ) != attestation.get("fingerprint"):
            return {}
        restored["durable_terminal_evidence"]["operator_attestation"] = {
            "attestation_id": attestation["attestation_id"],
            "attested_at": attestation["attested_at"],
            "statement": attestation["statement"],
            "strategy_snapshot_source": attestation[
                "strategy_snapshot_source"
            ],
            "fingerprint": attestation["fingerprint"],
        }
    return restored


def terminal_run_binding_from_operator_attestation(
    record: ActiveBattleIdentityRecord,
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    expected_identity_fingerprint: str,
    activity_scope_run_id: Optional[str],
) -> dict[str, Any]:
    """Bind one legacy Game Over through a narrow trusted-operator receipt."""

    expected_identity = str(expected_identity_fingerprint or "").strip()

    def unbound(reason: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "unbound",
            "reason": reason,
            "activity_scope_run_id": (
                str(activity_scope_run_id or "").strip() or None
            ),
            "active_round_identity_fingerprint": expected_identity or None,
        }

    if not isinstance(record, ActiveBattleIdentityRecord):
        return unbound("retained_active_battle_unavailable")
    if (
        _SHA256_RE.fullmatch(expected_identity) is None
        or expected_identity != record.fingerprint
    ):
        return unbound("operator_attestation_battle_identity_mismatch")
    attestation = _validated_operator_terminal_attestation(
        record.operator_terminal_attestation,
        identity_fingerprint=expected_identity,
        strategy_snapshot=record.strategy_snapshot,
    )
    if attestation is None:
        return unbound("operator_terminal_attestation_unavailable")
    if not (
        isinstance(acquisition, PlayerSaveAcquisitionBundle)
        and acquisition.complete
        and acquisition.acquisition_type
        is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        and acquisition.boundary is not None
        and acquisition.boundary.kind is PlayerSaveBoundaryKind.GAME_OVER
        and acquisition.boundary.active_round_identity_fingerprint
        == expected_identity
    ):
        return unbound("terminal_natural_boundary_mismatch")
    runtime_evidence = attestation["runtime"]
    if acquisition.binding_fingerprint != runtime_evidence.get(
        "target_binding_fingerprint"
    ):
        return unbound("operator_attested_terminal_target_changed")
    runtime = getattr(acquisition.snapshot, "runtime_save", None)
    if getattr(runtime, "round_active", None) is not False:
        return unbound("terminal_save_still_active")
    return {
        "schema_version": 1,
        "status": "bound",
        "reason": "trusted_operator_attested_legacy_terminal_continuity",
        "binding_source": "operator_terminal_attestation",
        "activity_scope_run_id": (
            str(activity_scope_run_id or "").strip() or None
        ),
        "active_round_identity_fingerprint": record.fingerprint,
        "operator_attestation_fingerprint": attestation["fingerprint"],
        "terminal_continuity": {
            "schema_version": 1,
            "comparison": "trusted_operator_attestation",
            "target_binding_fingerprint": acquisition.binding_fingerprint,
            "attested_at": attestation["attested_at"],
        },
    }


def terminal_run_binding_from_round_counters(
    record: ActiveBattleIdentityRecord,
    acquisition: PlayerSaveAcquisitionBundle,
    *,
    expected_identity_fingerprint: str,
    activity_scope_run_id: Optional[str],
) -> dict[str, Any]:
    """Bind an inactive Game Over save by exact battle-start counter equality."""

    expected_identity = str(expected_identity_fingerprint or "").strip()

    def unbound(reason: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "unbound",
            "reason": reason,
            "activity_scope_run_id": (
                str(activity_scope_run_id or "").strip() or None
            ),
            "active_round_identity_fingerprint": expected_identity or None,
        }

    if not isinstance(record, ActiveBattleIdentityRecord):
        return unbound("retained_active_battle_unavailable")
    if (
        _SHA256_RE.fullmatch(expected_identity) is None
        or expected_identity != record.fingerprint
    ):
        return unbound("restart_handoff_battle_identity_mismatch")
    continuity = record.terminal_continuity
    if continuity is None:
        return unbound("retained_round_counter_vector_unavailable")
    if not (
        isinstance(acquisition, PlayerSaveAcquisitionBundle)
        and acquisition.complete
        and acquisition.acquisition_type
        is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        and acquisition.boundary is not None
        and acquisition.boundary.kind is PlayerSaveBoundaryKind.GAME_OVER
        and acquisition.boundary.active_round_identity_fingerprint
        == expected_identity
    ):
        return unbound("terminal_natural_boundary_mismatch")
    if acquisition.binding_fingerprint != (
        continuity.target_binding_fingerprint
    ):
        return unbound("terminal_save_target_changed")

    snapshot = acquisition.snapshot
    runtime = getattr(snapshot, "runtime_save", None)
    vector = getattr(runtime, "round_counter_vector", None)
    terminal_revision = getattr(runtime, "save_revision", None)
    snapshot_revision = getattr(snapshot, "save_revision", None)
    if getattr(runtime, "round_active", None) is not False:
        return unbound("terminal_save_still_active")
    if not (
        getattr(runtime, "round_counter_vector_status", None) == "observed"
        and getattr(runtime, "round_counter_vector_reason", None) == ""
        and isinstance(vector, RoundCounterVectorEvidence)
    ):
        return unbound("terminal_round_counter_vector_unavailable")
    if vector.tier_count != continuity.round_counter_tier_count:
        return unbound("terminal_round_counter_vector_shape_changed")
    if vector.fingerprint != continuity.round_counter_vector_fingerprint:
        return unbound("terminal_round_counter_vector_changed")
    if not (
        getattr(runtime, "save_revision_status", None) == "observed"
        and getattr(runtime, "save_revision_reason", None) == ""
        and type(terminal_revision) is int
        and snapshot_revision == terminal_revision
        and terminal_revision >= continuity.save_revision
    ):
        return unbound("terminal_save_revision_regressed_or_unavailable")

    return {
        "schema_version": 1,
        "status": "bound",
        "reason": "retained_round_counter_vector_matches_terminal_save",
        "binding_source": "durable_full_round_counter_vector",
        "activity_scope_run_id": (
            str(activity_scope_run_id or "").strip() or None
        ),
        "active_round_identity_fingerprint": record.fingerprint,
        "terminal_continuity": {
            "schema_version": 1,
            "comparison": "exact_full_vector_match",
            "round_counter_tier_count": continuity.round_counter_tier_count,
            "active_save_revision": continuity.save_revision,
            "terminal_save_revision": terminal_revision,
            "target_binding_fingerprint": (
                continuity.target_binding_fingerprint
            ),
        },
    }


def _detached_json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    detached = json.loads(
        json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if not isinstance(detached, dict):
        raise TypeError("detached evidence must remain a mapping")
    return detached


def _mapping_fingerprint(value: Mapping[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "fingerprint"}
    return _json_fingerprint(material)


def _strategy_snapshot_material(
    *,
    identity_fingerprint: str,
    strategy: str,
    strategy_definition_fingerprint: str,
    session_preflight_configuration_fingerprint: str,
    run_configuration: Mapping[str, Any],
    recorded_at: datetime,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one immutable, self-fingerprinted Strategy report snapshot."""

    detached_configuration = _detached_json_mapping(run_configuration)
    detached_provenance = _detached_json_mapping(provenance)
    material = {
        "schema_version": 1,
        "identity_fingerprint": identity_fingerprint,
        "strategy": strategy,
        "strategy_definition_fingerprint": (
            strategy_definition_fingerprint
        ),
        "session_preflight_configuration_fingerprint": (
            session_preflight_configuration_fingerprint
        ),
        "run_configuration": detached_configuration,
        "run_configuration_fingerprint": _json_fingerprint(
            detached_configuration
        ),
        "recorded_at": _aware_timestamp(recorded_at).isoformat(),
        "provenance": detached_provenance,
    }
    material["fingerprint"] = _mapping_fingerprint(material)
    return material


def _json_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware_timestamp(value: Optional[datetime]) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise BattleIdentityStoreError(
            "battle identity record is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise BattleIdentityStoreError("battle identity root is malformed")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(dict(payload), temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


__all__ = [
    "ActiveBattleEmulatorHandoffGuard",
    "ActiveBattleIdentityCoordinator",
    "ActiveBattleIdentityRecord",
    "ActiveBattleProgressCheckpoint",
    "ActiveBattleTerminalContinuity",
    "BattleIdentityCheckContext",
    "BattleIdentityCheckResult",
    "BattleIdentityCheckStatus",
    "BattleIdentityRelation",
    "BattleIdentityStore",
    "BattleIdentityContinuityError",
    "BattleIdentityStoreError",
    "durable_terminal_report_evidence_from_record",
    "terminal_run_binding_from_operator_attestation",
    "terminal_run_binding_from_round_counters",
]
