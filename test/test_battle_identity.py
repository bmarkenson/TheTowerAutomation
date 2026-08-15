from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.battle_identity import (
    ActiveBattleIdentityCoordinator,
    BattleIdentityCheckContext,
    BattleIdentityCheckStatus,
    BattleIdentityRelation,
    BattleIdentityStore,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_serialization import (
    GuardedSerializationResult,
    GuardedSerializationStatus,
)
from core.runtime_save import ActiveRoundIdentity


def _identity(*, seed: int = 12345, counter: int = 9) -> ActiveRoundIdentity:
    values = {
        "game_version": 1102,
        "current_tier": 19,
        "rounds_started_this_tier": counter,
        "round_seed": seed,
    }
    rendered = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return ActiveRoundIdentity(
        **values,
        fingerprint=hashlib.sha256(rendered).hexdigest(),
    )


def _acquisition(
    identity: ActiveRoundIdentity | None,
) -> PlayerSaveAcquisitionBundle:
    now = datetime.now(timezone.utc)
    snapshot = SimpleNamespace(
        runtime_save=SimpleNamespace(
            round_active=identity is not None,
            active_round_identity=identity,
        )
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="stable_player_save_decoded",
        binding=PlayerSaveTargetBinding("localhost:5555", 7),
        acquisition_started_at=now,
        captured_at=now,
        acquisition_completed_at=now,
        transport_stable=True,
        snapshot=snapshot,
    )


def _coordinator(
    tmp_path,
    context: BattleIdentityCheckContext,
) -> ActiveBattleIdentityCoordinator:
    acquirer = StablePlayerSaveAcquirer(
        fixed_target="localhost:5555",
        pull_fn=lambda *_args, **_kwargs: b"unused",
        decode_fn=lambda *_args, **_kwargs: SimpleNamespace(),
    )
    return ActiveBattleIdentityCoordinator(
        acquirer=acquirer,
        store=BattleIdentityStore(tmp_path / "battle_identity.json"),
        context_fn=lambda: context,
        source_guard_fn=lambda _frame, _stable: True,
    )


def test_store_uses_active_round_identity_for_same_and_later_battles(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    first = _identity()
    acquisition = _acquisition(first)

    record, relation = store.bind(
        first,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=acquisition,
    )

    assert relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert record.fingerprint == first.fingerprint
    assert store.record_session_preflight(
        identity_fingerprint=first.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="a" * 64,
        evidence={"valid": True, "failed_checks": []},
    )

    same, relation = store.bind(
        first,
        reason="automation_resumed",
        operation_id="resume-1",
        acquisition=acquisition,
    )
    assert relation is BattleIdentityRelation.SAME_BATTLE
    assert same.session_preflight is not None

    later_identity = _identity(seed=54321, counter=10)
    later, relation = store.bind(
        later_identity,
        reason="automation_resumed",
        operation_id="resume-2",
        acquisition=_acquisition(later_identity),
    )
    assert relation is BattleIdentityRelation.LATER_BATTLE
    assert later.fingerprint == later_identity.fingerprint
    assert later.session_preflight is None


def test_forced_inactive_save_closes_retained_active_identity(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )

    store.mark_inactive(
        reason="home_new_battle",
        operation_id="home-1",
        acquisition=_acquisition(None),
    )

    assert store.active() is None
    payload = json.loads((tmp_path / "battle_identity.json").read_text())
    assert payload["status"] == "inactive"
    assert payload["previous_active_identity"]["fingerprint"] == (
        identity.fingerprint
    )


def test_coordinator_binds_only_the_forced_active_identity(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="launch-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    identity = _identity()
    acquisition = _acquisition(identity)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=acquisition,
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ) as acquire:
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="battle_started",
            initial_frame=object(),
        )

    assert result.complete
    assert result.identity == identity
    assert result.relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert result.recapture_required
    acquire.assert_called_once()


def test_coordinator_refuses_changed_identity_during_exact_verification(
    tmp_path,
):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="cleanup-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    identity = _identity(seed=99999)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=_acquisition(identity),
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="exclusive_validation_cleanup",
            expected_identity_fingerprint=_identity().fingerprint,
        )

    assert result.status is BattleIdentityCheckStatus.BLOCKED
    assert result.reason == "active_round_identity_changed"
    assert BattleIdentityStore(tmp_path / "battle_identity.json").active() is None


def test_coordinator_never_falls_back_from_inactive_or_unreadable_save(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="attach-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=_acquisition(None),
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="attach_battle",
        )

    assert result.status is BattleIdentityCheckStatus.UNAVAILABLE
    assert result.reason == (
        "active_round_identity_unavailable_after_forced_serialization"
    )
    assert result.source_restored
    assert result.recapture_required


def test_coordinator_preserves_serializer_block_classification(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="attach-1",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    coordinator = _coordinator(tmp_path, context)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.BLOCKED,
        "restored_target_binding_unverified",
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with patch(
        "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
        return_value=serialized,
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=Mock(return_value=True),
            reason="attach_battle",
        )

    assert result.status is BattleIdentityCheckStatus.BLOCKED
    assert result.reason == "restored_target_binding_unverified"
    assert result.source_restored
    assert result.recapture_required
