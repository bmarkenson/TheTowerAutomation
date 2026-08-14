"""Neutral ownership context for globally shared player-save observations."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from core.player_save_acquisition import PlayerSaveTargetBinding


@dataclass(frozen=True, repr=False)
class PlayerSaveObservationContext:
    """Exact private process, activity, target, and generation binding."""

    runtime_session_id: str = field(repr=False)
    activity_scope_id: str = field(repr=False)
    target_binding: PlayerSaveTargetBinding = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_session_id",
            str(self.runtime_session_id or "").strip(),
        )
        object.__setattr__(
            self,
            "activity_scope_id",
            str(self.activity_scope_id or "").strip(),
        )
        if not isinstance(self.target_binding, PlayerSaveTargetBinding):
            raise TypeError(
                "player-save observation requires a typed target binding"
            )

    def valid(self) -> bool:
        return bool(
            self.runtime_session_id
            and self.activity_scope_id
            and isinstance(self.target_binding, PlayerSaveTargetBinding)
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "runtime_session_fingerprint": _fingerprint_text(
                self.runtime_session_id
            ),
            "activity_scope_fingerprint": _fingerprint_text(
                self.activity_scope_id
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
