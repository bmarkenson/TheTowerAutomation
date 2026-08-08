"""Small typed player-save temporal fixtures shared by focused tests."""

from __future__ import annotations

from typing import Any, Mapping

from core.player_save_acquisition import (
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_temporal import (
    RunningAttachmentSaveFact,
    RunningAttachmentSaveObservations,
    RunningAttachmentTemporalBinding,
    attachment_temporal_class,
)


def running_attachment_observations(
    checks: Mapping[str, Any],
    *,
    runtime_session_id: str = "runtime-1",
    source_scope_id: str = "scope-1",
    final_scope_id: str | None = None,
    bind_final: bool = True,
    target: str = "private-target",
    target_generation: int = 3,
    mapping_id: str = "data-9-game-1073",
    round_identity: str = "active-round-fingerprint",
    captured_at: str = "2026-08-06T23:31:05+00:00",
) -> RunningAttachmentSaveObservations:
    facts = []
    for check_id, raw in checks.items():
        evidence = (
            raw
            if isinstance(raw, Mapping)
            and ("value" in raw or "source_fields" in raw)
            else {"value": raw}
        )
        facts.append(
            RunningAttachmentSaveFact(
                check_id=check_id,
                temporal_class=attachment_temporal_class(check_id),
                value=evidence.get("value"),
                source_fields=tuple(evidence.get("source_fields") or ()),
            )
        )
    observations = RunningAttachmentSaveObservations(
        binding=RunningAttachmentTemporalBinding(
            runtime_session_id=runtime_session_id,
            source_activity_scope_id=source_scope_id,
            target_binding=PlayerSaveTargetBinding(target, target_generation),
            mapping_id=mapping_id,
            active_round_identity_fingerprint=round_identity,
            captured_at=captured_at,
            acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        ),
        facts=tuple(facts),
    )
    if not bind_final:
        return observations
    return observations.bind_final_scope(final_scope_id or source_scope_id)


__all__ = ["running_attachment_observations"]
