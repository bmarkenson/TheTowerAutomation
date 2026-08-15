"""Neutral ownership context for globally shared player-save observations."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Optional

from core.player_save_acquisition import PlayerSaveTargetBinding


@dataclass(frozen=True, repr=False)
class PlayerSaveObservationContext:
    """Exact process, save battle identity, target, and optional report segment."""

    runtime_session_id: str = field(repr=False)
    activity_scope_id: Optional[str] = field(compare=False, repr=False)
    active_round_identity_fingerprint: str = field(repr=False)
    target_binding: PlayerSaveTargetBinding = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_session_id",
            str(self.runtime_session_id or "").strip(),
        )
        report_scope = str(self.activity_scope_id or "").strip()
        object.__setattr__(self, "activity_scope_id", report_scope or None)
        identity = str(
            self.active_round_identity_fingerprint or ""
        ).strip().lower()
        object.__setattr__(
            self,
            "active_round_identity_fingerprint",
            identity,
        )
        if len(identity) != 64 or any(
            character not in "0123456789abcdef" for character in identity
        ):
            raise ValueError(
                "player-save observation requires an active-round identity"
            )
        if not isinstance(self.target_binding, PlayerSaveTargetBinding):
            raise TypeError(
                "player-save observation requires a typed target binding"
            )

    def valid(self) -> bool:
        return bool(
            self.runtime_session_id
            and self.active_round_identity_fingerprint
            and isinstance(self.target_binding, PlayerSaveTargetBinding)
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "runtime_session_fingerprint": _fingerprint_text(
                self.runtime_session_id
            ),
            "activity_scope_fingerprint": _fingerprint_text(
                self.activity_scope_id or "report-scope-unavailable"
            ),
            "active_round_identity_fingerprint": (
                self.active_round_identity_fingerprint
            ),
            "target_binding_fingerprint": self.target_binding.fingerprint,
        }

    def __repr__(self) -> str:
        return (
            "PlayerSaveObservationContext("
            f"binding='{self.target_binding.fingerprint[:16]}...')"
        )


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


__all__ = ["PlayerSaveObservationContext"]
