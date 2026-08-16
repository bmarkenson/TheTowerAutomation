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
    survival_activation_checkpoint: Optional[Mapping[str, Any]] = field(
        default=None,
        repr=False,
    )
    terminal_continuity: Optional[ActiveBattleTerminalContinuity] = field(
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
            if previous is None:
                relation = BattleIdentityRelation.FIRST_OBSERVATION
            elif previous.fingerprint == normalized_identity.fingerprint:
                relation = BattleIdentityRelation.SAME_BATTLE
            else:
                relation = BattleIdentityRelation.LATER_BATTLE

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
            if (
                relation is BattleIdentityRelation.SAME_BATTLE
                and previous is not None
            ):
                for key, value in (
                    ("session_preflight", previous.session_preflight),
                    ("strategy_snapshot", previous.strategy_snapshot),
                    (
                        "survival_activation_checkpoint",
                        previous.survival_activation_checkpoint,
                    ),
                ):
                    if value is not None:
                        payload[key] = dict(value)
            self._write_payload(payload)
            return _record_from_payload(payload), relation

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
        material = {
            "schema_version": 1,
            "identity_fingerprint": expected,
            "strategy": strategy_name,
            "strategy_definition_fingerprint": definition_fingerprint,
            "session_preflight_configuration_fingerprint": (
                preflight_fingerprint
            ),
            "run_configuration": detached_configuration,
            "run_configuration_fingerprint": _json_fingerprint(
                detached_configuration
            ),
            "recorded_at": _aware_timestamp(recorded_at).isoformat(),
        }
        material["fingerprint"] = _mapping_fingerprint(material)
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
    survival_activation_checkpoint = (
        _validated_survival_activation_checkpoint(
            payload.get("survival_activation_checkpoint"),
            identity_fingerprint=(identity.fingerprint if identity else ""),
        )
    )
    terminal_continuity_value = payload.get("terminal_continuity")
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
        survival_activation_checkpoint=(
            dict(survival_activation_checkpoint)
            if isinstance(survival_activation_checkpoint, Mapping)
            else None
        ),
        terminal_continuity=terminal_continuity,
    )


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
    """Restore component evidence only after exact terminal vector proof."""

    if not isinstance(record, ActiveBattleIdentityRecord):
        return {}
    identity = str(record.fingerprint or "").strip()
    if not (
        isinstance(terminal_binding, Mapping)
        and terminal_binding.get("status") == "bound"
        and terminal_binding.get("binding_source")
        == "durable_full_round_counter_vector"
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

        receipt = _validated_session_preflight(
            record.session_preflight,
            identity_fingerprint=identity,
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

    if not components:
        return {}

    restored["durable_terminal_evidence"] = {
        "schema_version": 1,
        "status": "restored",
        "binding_source": "durable_full_round_counter_vector",
        "active_round_identity_fingerprint": identity,
        "components": components,
        "component_fingerprints": component_fingerprints,
    }
    return restored


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
    "ActiveBattleIdentityCoordinator",
    "ActiveBattleIdentityRecord",
    "ActiveBattleTerminalContinuity",
    "BattleIdentityCheckContext",
    "BattleIdentityCheckResult",
    "BattleIdentityCheckStatus",
    "BattleIdentityRelation",
    "BattleIdentityStore",
    "BattleIdentityStoreError",
    "durable_terminal_report_evidence_from_record",
    "terminal_run_binding_from_round_counters",
]
