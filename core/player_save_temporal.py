"""Temporal authority for save facts bound to one running attachment.

Acquisition provenance answers how bytes were obtained.  The types here answer
how long a projected fact may remain true.  They deliberately own no ADB read,
Android lifecycle action, UI fallback, or global snapshot cache.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any, Callable, Optional

from core.player_save_acquisition import (
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)


class PlayerSaveTemporalClass(str, Enum):
    """The time interval over which one mapped save fact is authoritative."""

    CURRENT_CONFIGURATION = "current_configuration"
    ROUND_INVARIANT = "round_invariant"
    POINT_IN_TIME = "point_in_time"
    MONOTONIC_ROUND_PREFIX = "monotonic_round_prefix"
    TERMINAL_FINAL = "terminal_final"
    BOUNDARY_CLEAR = "boundary_clear"


ROUND_INVARIANT_ATTACHMENT_CHECKS = frozenset(
    {
        "workshop_preset",
        "guardian_chips",
        "bots_preset",
        "modules",
    }
)
POINT_IN_TIME_ATTACHMENT_CHECKS = frozenset({"cards_deck"})


def attachment_temporal_class(check_id: str) -> PlayerSaveTemporalClass:
    """Classify an allowlisted active-attachment configuration check."""

    normalized = str(check_id or "").strip()
    if normalized in ROUND_INVARIANT_ATTACHMENT_CHECKS:
        return PlayerSaveTemporalClass.ROUND_INVARIANT
    if normalized in POINT_IN_TIME_ATTACHMENT_CHECKS:
        return PlayerSaveTemporalClass.POINT_IN_TIME
    return PlayerSaveTemporalClass.CURRENT_CONFIGURATION


@dataclass(frozen=True, repr=False)
class RunningAttachmentTemporalBinding:
    """Private exact binding for facts obtained while a battle is running."""

    runtime_session_id: str = field(repr=False)
    source_activity_scope_id: str = field(repr=False)
    target_binding: PlayerSaveTargetBinding = field(repr=False)
    mapping_id: str
    active_round_identity_fingerprint: str
    captured_at: str
    acquisition_type: PlayerSaveAcquisitionType
    activity_scope_id: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        required = {
            "runtime_session_id": self.runtime_session_id,
            "source_activity_scope_id": self.source_activity_scope_id,
            "mapping_id": self.mapping_id,
            "active_round_identity_fingerprint": (
                self.active_round_identity_fingerprint
            ),
            "captured_at": self.captured_at,
        }
        for name, value in required.items():
            normalized = str(value or "").strip()
            if not normalized:
                raise ValueError(f"running attachment requires {name}")
            object.__setattr__(self, name, normalized)
        if not isinstance(self.target_binding, PlayerSaveTargetBinding):
            raise TypeError("running attachment requires a typed target binding")
        if self.acquisition_type is not PlayerSaveAcquisitionType.FORCED_SERIALIZATION:
            raise ValueError(
                "running attachment facts require forced serialization"
            )
        if self.activity_scope_id is not None:
            normalized_scope = str(self.activity_scope_id or "").strip()
            if not normalized_scope:
                raise ValueError("bound activity scope cannot be empty")
            object.__setattr__(self, "activity_scope_id", normalized_scope)

    @property
    def final(self) -> bool:
        return self.activity_scope_id is not None

    @property
    def claim_fingerprint(self) -> str:
        """Stable comparison key without exposing private binding values."""

        if self.activity_scope_id is None:
            raise ValueError("temporal claim is not bound to a final scope")
        return _fingerprint(
            "round-claim",
            self.mapping_id,
            self.target_binding.fingerprint,
            self.activity_scope_id,
            self.active_round_identity_fingerprint,
        )

    def bind_final_scope(
        self,
        activity_scope_id: str,
    ) -> "RunningAttachmentTemporalBinding":
        """Bind only after continuity persisted the authoritative final scope."""

        normalized = str(activity_scope_id or "").strip()
        if not normalized:
            raise ValueError("final activity scope is required")
        if self.activity_scope_id is not None and self.activity_scope_id != normalized:
            raise ValueError("running attachment is already bound to another scope")
        return replace(self, activity_scope_id=normalized)

    def matches_context(self, context: Any) -> bool:
        """Revalidate current process, final scope, target, and generation."""

        if self.activity_scope_id is None or context is None:
            return False
        try:
            return bool(
                str(context.runtime_session_id) == self.runtime_session_id
                and str(context.activity_scope_id) == self.activity_scope_id
                and str(context.target) == self.target_binding.target
                and int(context.target_generation)
                == self.target_binding.generation
                and context.active_battle_observed is True
            )
        except (AttributeError, TypeError, ValueError):
            return False

    def redacted(self) -> dict[str, Any]:
        if self.activity_scope_id is None:
            raise ValueError("unbound temporal provenance cannot be published")
        return {
            "schema_version": 1,
            "mapping_id": self.mapping_id,
            "target_generation": self.target_binding.fingerprint,
            "activity_scope": _fingerprint(
                "activity-scope", self.activity_scope_id
            ),
            "round_identity": self.active_round_identity_fingerprint,
            "captured_at": self.captured_at,
            "acquisition_type": self.acquisition_type.value,
            "claim_fingerprint": self.claim_fingerprint,
        }


@dataclass(frozen=True)
class RunningAttachmentSaveFact:
    check_id: str
    temporal_class: PlayerSaveTemporalClass
    value: Any = field(repr=False)
    source_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized = str(self.check_id or "").strip()
        if not normalized:
            raise ValueError("running attachment fact requires a check id")
        if not isinstance(self.temporal_class, PlayerSaveTemporalClass):
            raise TypeError("running attachment fact requires a temporal class")
        object.__setattr__(self, "check_id", normalized)
        object.__setattr__(
            self,
            "source_fields",
            tuple(str(value) for value in self.source_fields),
        )
        object.__setattr__(self, "value", deepcopy(self.value))

    def copied_value(self) -> Any:
        return deepcopy(self.value)


@dataclass(frozen=True, repr=False)
class RunningAttachmentSaveObservations:
    """Complete allowlisted facts from one forced running-save boundary."""

    binding: RunningAttachmentTemporalBinding = field(repr=False)
    facts: tuple[RunningAttachmentSaveFact, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RunningAttachmentTemporalBinding):
            raise TypeError("running observations require a temporal binding")
        normalized = tuple(self.facts)
        if not normalized or any(
            not isinstance(fact, RunningAttachmentSaveFact)
            for fact in normalized
        ):
            raise ValueError("running observations require typed facts")
        ids = [fact.check_id for fact in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("running observation check ids must be unique")
        object.__setattr__(self, "facts", normalized)

    def bind_final_scope(
        self,
        activity_scope_id: str,
    ) -> "RunningAttachmentSaveObservations":
        return replace(
            self,
            binding=self.binding.bind_final_scope(activity_scope_id),
        )

    def matches_context(self, context: Any) -> bool:
        return self.binding.matches_context(context)

    def fact(self, check_id: str) -> Optional[RunningAttachmentSaveFact]:
        normalized = str(check_id or "").strip()
        return next(
            (fact for fact in self.facts if fact.check_id == normalized),
            None,
        )

    def redacted_provenance(
        self,
        fact: RunningAttachmentSaveFact,
    ) -> dict[str, Any]:
        if fact not in self.facts:
            raise ValueError("fact does not belong to this attachment")
        return {
            **self.binding.redacted(),
            "temporal_class": fact.temporal_class.value,
            "save_checks": [fact.check_id],
            "source_fields": list(fact.source_fields),
        }


@dataclass
class BoundRunningAttachmentSaveEvidence:
    """One-use consumer view that rechecks scope/target at consumption time."""

    observations: RunningAttachmentSaveObservations
    context_fn: Callable[[], Any] = field(repr=False)
    _consumed: set[str] = field(default_factory=set, init=False, repr=False)
    _invalidated: bool = field(default=False, init=False, repr=False)

    def consume(self, check_id: str) -> Any:
        normalized = str(check_id or "").strip()
        if self._invalidated or normalized in self._consumed:
            return None
        try:
            context = self.context_fn()
        except Exception:
            self._invalidated = True
            return None
        if not self.observations.matches_context(context):
            self._invalidated = True
            return None
        fact = self.observations.fact(normalized)
        if (
            fact is None
            or fact.temporal_class is not PlayerSaveTemporalClass.ROUND_INVARIANT
        ):
            return None
        self._consumed.add(normalized)
        return fact.copied_value()


def canonical_temporal_value(value: Any) -> str:
    """Return a stable comparison form for normalized allowlisted values."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(label: str, *values: str) -> str:
    return hashlib.sha256(
        (
            f"thetower-player-save-temporal-{label}-v1\0"
            + "\0".join(str(value) for value in values)
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "BoundRunningAttachmentSaveEvidence",
    "POINT_IN_TIME_ATTACHMENT_CHECKS",
    "PlayerSaveTemporalClass",
    "ROUND_INVARIANT_ATTACHMENT_CHECKS",
    "RunningAttachmentSaveFact",
    "RunningAttachmentSaveObservations",
    "RunningAttachmentTemporalBinding",
    "attachment_temporal_class",
    "canonical_temporal_value",
]
