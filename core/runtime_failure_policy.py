"""Global disposition policy for runtime failures.

The runtime may repair a recoverable problem at a boundary where correction is
already safe.  Otherwise it keeps automation running and records degraded
evidence.  Only failures that make further device input unsafe may create an
automatic global Pause.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuntimeFailureKind(str, Enum):
    """Typed failure classes understood by the global runtime policy."""

    CONFIGURATION_MISMATCH = "configuration_mismatch"
    VALIDATION_UNAVAILABLE = "validation_unavailable"
    REPAIR_EXHAUSTED = "repair_exhausted"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    REPORTING_FAILURE = "reporting_failure"
    WORKFLOW_EVIDENCE_EXPIRED = "workflow_evidence_expired"

    CONTROL_AUTHORITY_LOST = "control_authority_lost"
    TARGET_OWNERSHIP_LOST = "target_ownership_lost"
    SOURCE_RESTORATION_LOST = "source_restoration_lost"
    INPUT_RESULT_UNCERTAIN = "input_result_uncertain"
    SAVE_CONTINUITY_LOST = "save_continuity_lost"


class RuntimeFailureDisposition(str, Enum):
    """The only three responses the global policy may select."""

    REPAIR_NOW = "repair_now"
    CONTINUE_DEGRADED = "continue_degraded"
    PAUSE_FOR_SAFETY = "pause_for_safety"


CATASTROPHIC_FAILURE_KINDS = frozenset(
    {
        RuntimeFailureKind.CONTROL_AUTHORITY_LOST,
        RuntimeFailureKind.TARGET_OWNERSHIP_LOST,
        RuntimeFailureKind.SOURCE_RESTORATION_LOST,
        RuntimeFailureKind.INPUT_RESULT_UNCERTAIN,
        RuntimeFailureKind.SAVE_CONTINUITY_LOST,
    }
)


@dataclass(frozen=True)
class RuntimeFailureDecision:
    """One policy answer, suitable for logs and workflow receipts."""

    kind: RuntimeFailureKind
    disposition: RuntimeFailureDisposition
    catastrophic: bool


def decide_runtime_failure(
    kind: RuntimeFailureKind,
    *,
    repair_available: bool = False,
) -> RuntimeFailureDecision:
    """Return the mandatory response for one typed runtime failure."""

    if not isinstance(kind, RuntimeFailureKind):
        raise TypeError("runtime failure kind must be a RuntimeFailureKind")
    catastrophic = kind in CATASTROPHIC_FAILURE_KINDS
    if catastrophic:
        disposition = RuntimeFailureDisposition.PAUSE_FOR_SAFETY
    elif repair_available:
        disposition = RuntimeFailureDisposition.REPAIR_NOW
    else:
        disposition = RuntimeFailureDisposition.CONTINUE_DEGRADED
    return RuntimeFailureDecision(
        kind=kind,
        disposition=disposition,
        catastrophic=catastrophic,
    )


__all__ = [
    "CATASTROPHIC_FAILURE_KINDS",
    "RuntimeFailureDecision",
    "RuntimeFailureDisposition",
    "RuntimeFailureKind",
    "decide_runtime_failure",
]
