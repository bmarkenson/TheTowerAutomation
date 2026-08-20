from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from automation.missions.manager import MissionManager
from core.action_authority import AuthorityHold
from core.app import App
from core.battle_activation_tracker import BattleActivationTracker
from core.battle_identity import (
    ActiveBattleIdentityCoordinator,
    BattleIdentityContinuityError,
    BattleIdentityCheckContext,
    BattleIdentityCheckResult,
    BattleIdentityCheckStatus,
    BattleIdentityRelation,
    BattleIdentityStore,
    BattleIdentityStoreError,
    durable_terminal_report_evidence_from_record,
    terminal_run_binding_from_operator_attestation,
    terminal_run_binding_from_round_counters,
)
from core.battle_lifecycle import HomeBattleControl
from core.home_battle import HomeBattleEvidence
from core.input import TapDispatchOutcome, TapDispatchStatus
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
    StablePlayerSaveAcquirer,
)
from core.player_save_serialization import (
    GuardedSerializationResult,
    GuardedSerializationStatus,
)
from core.player_save_preflight import (
    PlayerSavePreflightResult,
    PlayerSavePreflightStatus,
)
from core.run_state import AUTOMATION, RunState
from core.runtime_save import (
    ActiveRoundIdentity,
    RoundCounterVectorEvidence,
)


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
    *,
    save_revision: int = 100,
    current_wave: int = 500,
    target_generation: int = 7,
    acquisition_type: PlayerSaveAcquisitionType = (
        PlayerSaveAcquisitionType.FORCED_SERIALIZATION
    ),
) -> PlayerSaveAcquisitionBundle:
    now = datetime.now(timezone.utc)
    vector = _round_counter_vector(identity) if identity is not None else None
    snapshot = SimpleNamespace(
        save_revision=save_revision,
        runtime_save=SimpleNamespace(
            round_active=identity is not None,
            active_round_identity=identity,
            save_revision=save_revision,
            save_revision_status="observed",
            save_revision_reason="",
            current_wave=current_wave,
            current_wave_status="observed",
            current_wave_reason="",
            round_counter_vector=vector,
            round_counter_vector_status=(
                "observed" if vector is not None else "unavailable"
            ),
            round_counter_vector_reason=(
                "" if vector is not None else "round_inactive_fixture"
            ),
        )
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=acquisition_type,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="stable_player_save_decoded",
        binding=PlayerSaveTargetBinding(
            "localhost:5555",
            target_generation,
        ),
        acquisition_started_at=now,
        captured_at=now,
        acquisition_completed_at=now,
        transport_stable=True,
        snapshot=snapshot,
    )


def _round_counter_vector(
    identity: ActiveRoundIdentity,
    *,
    incremented_tier: int | None = None,
) -> RoundCounterVectorEvidence:
    counters = [0] * 40
    counters[identity.current_tier] = identity.rounds_started_this_tier
    if incremented_tier is not None:
        counters[incremented_tier] += 1
    rendered = json.dumps(
        {
            "schema_version": 1,
            "game_version": identity.game_version,
            "rounds_started_this_tier": counters,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return RoundCounterVectorEvidence(
        tier_count=len(counters),
        fingerprint=hashlib.sha256(rendered).hexdigest(),
    )


def _terminal_acquisition(
    identity: ActiveRoundIdentity,
    *,
    incremented_tier: int | None = None,
    save_revision: int = 101,
    target_generation: int = 7,
) -> PlayerSaveAcquisitionBundle:
    now = datetime.now(timezone.utc)
    vector = _round_counter_vector(
        identity,
        incremented_tier=incremented_tier,
    )
    snapshot = SimpleNamespace(
        save_revision=save_revision,
        runtime_save=SimpleNamespace(
            round_active=False,
            active_round_identity=None,
            save_revision=save_revision,
            save_revision_status="observed",
            save_revision_reason="",
            round_counter_vector=vector,
            round_counter_vector_status="observed",
            round_counter_vector_reason="",
        ),
    )
    boundary = PlayerSaveNaturalBoundary(
        kind=PlayerSaveBoundaryKind.GAME_OVER,
        observed_at=now,
        runtime_session_id="runtime-2",
        activity_scope_id="scope-1",
        active_round_identity_fingerprint=identity.fingerprint,
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="stable_player_save_decoded",
        binding=PlayerSaveTargetBinding(
            "localhost:5555",
            target_generation,
        ),
        acquisition_started_at=now,
        captured_at=now,
        acquisition_completed_at=now,
        transport_stable=True,
        snapshot=snapshot,
        boundary=boundary,
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
    assert record.terminal_continuity is not None
    assert record.terminal_continuity.round_counter_tier_count == 40
    assert record.terminal_continuity.save_revision == 100
    assert record.progress_checkpoint is not None
    assert record.progress_checkpoint.max_save_revision == 100
    assert record.progress_checkpoint.max_current_wave == 500
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


def test_shared_active_save_observations_advance_handoff_high_water(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    source = PlayerSaveTargetBinding("localhost:5555", 7)
    destination = PlayerSaveTargetBinding("localhost:5555", 8)
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    passive = _acquisition(
        identity,
        save_revision=104,
        current_wave=540,
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
    )

    assert store.record_progress_checkpoint(
        identity_fingerprint=identity.fingerprint,
        target_binding=source,
        acquisition=passive,
    )
    guard = store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=source,
        destination_target_binding=destination,
        source_wave=550,
    )

    assert guard is not None
    assert guard.source_save_revision == 104
    assert guard.source_wave == 550
    destination_passive = _acquisition(
        identity,
        save_revision=999,
        current_wave=999,
        target_generation=8,
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
    )
    assert not store.record_progress_checkpoint(
        identity_fingerprint=identity.fingerprint,
        target_binding=destination,
        acquisition=destination_passive,
    )
    retained = store.active()
    assert retained is not None
    assert retained.progress_checkpoint is not None
    assert retained.progress_checkpoint.max_save_revision == 104
    assert retained.progress_checkpoint.max_current_wave == 540

    accepted, relation = store.bind(
        identity,
        reason="destination_reconciliation",
        operation_id="move-1-check",
        acquisition=_acquisition(
            identity,
            save_revision=105,
            current_wave=550,
            target_generation=8,
        ),
    )

    assert relation is BattleIdentityRelation.SAME_BATTLE
    assert accepted.emulator_handoff_guard is None
    assert accepted.progress_checkpoint is not None
    assert accepted.progress_checkpoint.max_save_revision == 105
    assert accepted.progress_checkpoint.max_current_wave == 550


def test_emulator_handoff_accepts_expected_same_battle_wave_rollback(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    source = PlayerSaveTargetBinding("localhost:5555", 7)
    destination = PlayerSaveTargetBinding("localhost:5555", 8)
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity, current_wave=500),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=source,
        destination_target_binding=destination,
        source_wave=520,
    )

    accepted, relation = store.bind(
        identity,
        reason="destination_reconciliation",
        operation_id="move-1-check",
        acquisition=_acquisition(
            identity,
            save_revision=101,
            current_wave=480,
            target_generation=8,
        ),
    )

    assert relation is BattleIdentityRelation.SAME_BATTLE
    assert accepted.emulator_handoff_guard is None
    assert accepted.progress_checkpoint is not None
    assert accepted.progress_checkpoint.max_save_revision == 101
    assert accepted.progress_checkpoint.max_current_wave == 500


def test_emulator_handoff_save_revision_rollback_is_sticky_until_inactive_boundary(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    source = PlayerSaveTargetBinding("localhost:5555", 7)
    destination = PlayerSaveTargetBinding("localhost:5555", 8)
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity, current_wave=500),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=source,
        destination_target_binding=destination,
        source_wave=520,
    )

    expected_reason = "emulator_handoff_save_revision_regressed"
    with pytest.raises(BattleIdentityContinuityError, match=expected_reason):
        store.bind(
            identity,
            reason="destination_reconciliation",
            operation_id="move-1-check",
            acquisition=_acquisition(
                identity,
                save_revision=99,
                current_wave=520,
                target_generation=8,
            ),
        )

    retained = store.active()
    assert retained is not None
    assert retained.emulator_handoff_guard is not None
    assert retained.emulator_handoff_guard.status == "blocked"
    assert retained.emulator_handoff_guard.failure_reason == expected_reason
    with pytest.raises(BattleIdentityContinuityError, match=expected_reason):
        store.arm_emulator_handoff_guard(
            request_id="move-2",
            identity_fingerprint=identity.fingerprint,
            source_target_binding=destination,
            destination_target_binding=PlayerSaveTargetBinding(
                "localhost:5555",
                9,
            ),
        )
    with pytest.raises(BattleIdentityContinuityError, match=expected_reason):
        store.bind(
            identity,
            reason="destination_reconciliation_retry",
            operation_id="move-1-check-2",
            acquisition=_acquisition(
                identity,
                save_revision=500,
                current_wave=1000,
                target_generation=8,
            ),
        )

    store.mark_inactive(
        reason="home_new_battle",
        operation_id="home-1",
        acquisition=_acquisition(
            None,
            save_revision=501,
            current_wave=0,
            target_generation=8,
        ),
    )
    assert store.active() is None


def test_legacy_wave_only_handoff_failure_is_accepted_once(tmp_path):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity, current_wave=500),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        destination_target_binding=PlayerSaveTargetBinding(
            "localhost:5555",
            8,
        ),
        source_wave=520,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["emulator_handoff_guard"].update(
        {
            "status": "blocked",
            "failure_reason": "emulator_handoff_current_wave_regressed",
            "observed_save_revision": 101,
            "observed_wave": 500,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    accepted = store.accept_expected_emulator_handoff_wave_rollback()

    assert accepted is not None
    assert accepted.source_wave == 520
    assert accepted.observed_wave == 500
    retained = store.active()
    assert retained is not None
    assert retained.emulator_handoff_guard is None
    assert store.accept_expected_emulator_handoff_wave_rollback() is None


def test_legacy_revision_regression_failure_remains_blocked(tmp_path):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        destination_target_binding=PlayerSaveTargetBinding(
            "localhost:5555",
            8,
        ),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["emulator_handoff_guard"].update(
        {
            "status": "blocked",
            "failure_reason": "emulator_handoff_save_revision_regressed",
            "observed_save_revision": 99,
            "observed_wave": 500,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.accept_expected_emulator_handoff_wave_rollback() is None
    retained = store.active()
    assert retained is not None
    assert retained.emulator_handoff_guard is not None
    assert (
        retained.emulator_handoff_guard.failure_reason
        == "emulator_handoff_save_revision_regressed"
    )


def test_emulator_handoff_rejects_a_different_active_battle(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        destination_target_binding=PlayerSaveTargetBinding(
            "localhost:5555",
            8,
        ),
    )
    different = _identity(seed=54321, counter=10)

    with pytest.raises(
        BattleIdentityContinuityError,
        match="emulator_handoff_active_identity_changed",
    ):
        store.bind(
            different,
            reason="destination_reconciliation",
            operation_id="move-1-check",
            acquisition=_acquisition(
                different,
                save_revision=200,
                current_wave=900,
                target_generation=8,
            ),
        )


def test_failed_target_move_can_cancel_only_its_prepared_handoff_guard(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    destination = PlayerSaveTargetBinding("localhost:5555", 8)
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    store.arm_emulator_handoff_guard(
        request_id="move-1",
        identity_fingerprint=identity.fingerprint,
        source_target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        destination_target_binding=destination,
    )

    assert not store.cancel_emulator_handoff_guard(
        request_id="different-move",
        destination_target_binding=destination,
    )
    assert store.cancel_emulator_handoff_guard(
        request_id="move-1",
        destination_target_binding=destination,
    )
    retained = store.active()
    assert retained is not None
    assert retained.emulator_handoff_guard is None


def test_store_preserves_battle_bound_strategy_and_activation_checkpoint(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    assert store.record_strategy_snapshot(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
    )
    record = store.active()
    assert record is not None
    assert record.strategy_snapshot is not None
    tracker = BattleActivationTracker()
    assert tracker.bind_round_identity(identity.fingerprint)
    tracker._record_visual_event(
        {
            "ability": "nuke",
            "sequence": 1,
            "approximate_wave": 2_500,
            "detection_source": "button_disappearance",
        }
    )
    checkpoint = tracker.checkpoint()
    assert checkpoint is not None
    tracker_configuration_fingerprint = checkpoint[
        "tracker_configuration_fingerprint"
    ]
    assert not store.record_survival_activation_checkpoint(
        identity_fingerprint=identity.fingerprint,
        tracker_configuration_fingerprint="f" * 64,
        checkpoint=checkpoint,
    )
    assert store.record_survival_activation_checkpoint(
        identity_fingerprint=identity.fingerprint,
        tracker_configuration_fingerprint=(
            tracker_configuration_fingerprint
        ),
        checkpoint=checkpoint,
    )

    same, relation = store.bind(
        identity,
        reason="automation_resumed",
        operation_id="resume-1",
        acquisition=_acquisition(identity),
    )
    assert relation is BattleIdentityRelation.SAME_BATTLE
    assert same.strategy_snapshot is not None
    assert same.survival_activation_checkpoint is not None

    later_identity = _identity(seed=54321, counter=10)
    later, relation = store.bind(
        later_identity,
        reason="automation_resumed",
        operation_id="resume-2",
        acquisition=_acquisition(later_identity),
    )
    assert relation is BattleIdentityRelation.LATER_BATTLE
    assert later.strategy_snapshot is None
    assert later.survival_activation_checkpoint is None


def test_malformed_optional_evidence_is_omitted_without_losing_identity(
    tmp_path,
):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    assert store.record_strategy_snapshot(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy_snapshot"]["run_configuration"]["tier"] = 20
    payload["survival_activation_checkpoint"] = {"schema_version": 1}
    path.write_text(json.dumps(payload), encoding="utf-8")

    record = store.active()

    assert record is not None
    assert record.fingerprint == identity.fingerprint
    assert record.strategy_snapshot is None
    assert record.survival_activation_checkpoint is None


def test_app_persists_and_restores_survival_checkpoint_for_exact_battle(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    strategy = SimpleNamespace(
        name="farm_t19",
        definition_fingerprint=lambda: "d" * 64,
        session_preflight_fingerprint=lambda: "e" * 64,
        run_configuration=lambda: {"profile": "farm", "tier": 19},
    )
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(
        strategy=strategy,
        active_battle_observed=lambda: True,
        awaiting_initial_battle_intent=lambda: False,
    )
    app._battle_identity_store = store
    app._retained_battle_identity_record = record
    app._active_round_identity_fingerprint = identity.fingerprint
    app._battle_activation_tracker = BattleActivationTracker()
    assert app._bind_survival_activation_tracker(identity.fingerprint)
    app._battle_activation_tracker._record_visual_event(
        {
            "ability": "nuke",
            "sequence": 1,
            "approximate_wave": 2_500,
            "detection_source": "button_disappearance",
        }
    )

    assert app._persist_battle_strategy_snapshot(identity.fingerprint)
    assert app._persist_survival_activation_checkpoint()

    replacement = App.__new__(App)
    replacement._retained_battle_identity_record = store.active()
    replacement._battle_activation_tracker = BattleActivationTracker()
    assert replacement._bind_survival_activation_tracker(identity.fingerprint)
    restored = replacement._battle_activation_tracker.snapshot()
    assert restored["nuke_activations"][0]["approximate_wave"] == 2_500


def test_app_persists_survival_checkpoint_without_strategy_snapshot(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    app = App.__new__(App)
    app._battle_identity_store = store
    app._retained_battle_identity_record = record
    app._active_round_identity_fingerprint = identity.fingerprint
    app._battle_activation_tracker = BattleActivationTracker()
    assert app._bind_survival_activation_tracker(identity.fingerprint)
    app._battle_activation_tracker._record_visual_event(
        {
            "ability": "second_wind",
            "sequence": 1,
            "approximate_wave": 2_400,
            "detection_source": "active_status_icon",
        }
    )

    assert app._persist_survival_activation_checkpoint()
    retained = store.active()
    assert retained is not None
    assert retained.strategy_snapshot is None
    assert retained.survival_activation_checkpoint is not None


def test_terminal_full_counter_vector_recovers_the_retained_battle(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )

    binding = terminal_run_binding_from_round_counters(
        record,
        _terminal_acquisition(identity),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )

    assert binding["status"] == "bound"
    assert binding["active_round_identity_fingerprint"] == identity.fingerprint
    assert binding["binding_source"] == "durable_full_round_counter_vector"
    assert record.terminal_continuity is not None
    assert binding["terminal_continuity"] == {
        "schema_version": 1,
        "comparison": "exact_full_vector_match",
        "round_counter_tier_count": 40,
        "active_save_revision": 100,
        "terminal_save_revision": 101,
        "target_binding_fingerprint": (
            record.terminal_continuity.target_binding_fingerprint
        ),
    }


@pytest.mark.parametrize("incremented_tier", (0, 19, 39))
def test_terminal_counter_increment_rejects_retained_battle(
    tmp_path,
    incremented_tier,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )

    binding = terminal_run_binding_from_round_counters(
        record,
        _terminal_acquisition(
            identity,
            incremented_tier=incremented_tier,
        ),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )

    assert binding["status"] == "unbound"
    assert binding["reason"] == "terminal_round_counter_vector_changed"


def test_terminal_counter_proof_rejects_revision_or_target_regression(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )

    old_revision = terminal_run_binding_from_round_counters(
        record,
        _terminal_acquisition(identity, save_revision=99),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )
    changed_target = terminal_run_binding_from_round_counters(
        record,
        _terminal_acquisition(identity, target_generation=8),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )

    assert old_revision["reason"] == (
        "terminal_save_revision_regressed_or_unavailable"
    )
    assert changed_target["reason"] == "terminal_save_target_changed"


def test_legacy_active_record_without_counter_vector_stays_readable(tmp_path):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("terminal_continuity")
    payload.pop("progress_checkpoint")
    path.write_text(json.dumps(payload), encoding="utf-8")

    record = store.active()

    assert record is not None
    assert record.terminal_continuity is None
    assert record.progress_checkpoint is None
    binding = terminal_run_binding_from_round_counters(
        record,
        _terminal_acquisition(identity),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )
    assert binding["status"] == "unbound"
    assert binding["reason"] == "retained_round_counter_vector_unavailable"


def test_legacy_preflight_receipt_restores_without_strategy_snapshot(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    record, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    assert store.record_session_preflight(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    record = store.active()
    assert record is not None
    assert record.strategy_snapshot is None

    restored = durable_terminal_report_evidence_from_record(
        record,
        terminal_binding={
            "schema_version": 1,
            "status": "bound",
            "binding_source": "durable_full_round_counter_vector",
            "active_round_identity_fingerprint": identity.fingerprint,
        },
    )

    assert restored["strategy"] == "farm_t19"
    assert restored["session_preflight_evidence"] == {
        "valid": True,
        "failed_checks": [],
    }
    assert "run_configuration" not in restored
    assert restored["durable_terminal_evidence"]["components"] == [
        "session_preflight_evidence"
    ]


def test_operator_attestation_atomically_restores_legacy_terminal_strategy(
    tmp_path,
):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("terminal_continuity")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.record_session_preflight(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    observed_at = datetime(2026, 8, 16, 19, 0, tzinfo=timezone.utc)

    assert store.record_operator_terminal_strategy_attestation(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
        runtime_id="runtime-current",
        pid=1234,
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        observation_id="runtime-current:42",
        observed_at=observed_at,
        reason="Operator confirmed this retained Game Over is unchanged",
        attested_at=observed_at,
    )
    record = store.active()
    assert record is not None
    assert record.terminal_continuity is None
    assert record.strategy_snapshot is not None
    assert record.strategy_snapshot["provenance"]["kind"] == (
        "operator_terminal_attestation"
    )
    assert record.operator_terminal_attestation is not None

    binding = terminal_run_binding_from_operator_attestation(
        record,
        _terminal_acquisition(identity),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )
    restored = durable_terminal_report_evidence_from_record(
        record,
        terminal_binding=binding,
    )

    assert binding["status"] == "bound"
    assert binding["binding_source"] == "operator_terminal_attestation"
    assert restored["strategy"] == "farm_t19"
    assert restored["run_configuration"] == {
        "profile": "farm",
        "tier": 19,
    }
    evidence = restored["durable_terminal_evidence"]
    assert evidence["binding_source"] == "operator_terminal_attestation"
    assert evidence["operator_attestation"]["statement"] == (
        "terminal_and_strategy_unchanged_since_battle"
    )
    assert evidence["operator_attestation"]["strategy_snapshot_source"] == (
        "operator_backfill"
    )


def test_operator_attestation_rejects_changed_target_and_tampering(tmp_path):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("terminal_continuity")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.record_session_preflight(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    observed_at = datetime.now(timezone.utc)
    assert store.record_operator_terminal_strategy_attestation(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
        runtime_id="runtime-current",
        pid=1234,
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        observation_id="runtime-current:42",
        observed_at=observed_at,
        reason="Operator confirmed this retained Game Over is unchanged",
    )
    record = store.active()
    assert record is not None

    changed_target = terminal_run_binding_from_operator_attestation(
        record,
        _terminal_acquisition(identity, target_generation=8),
        expected_identity_fingerprint=identity.fingerprint,
        activity_scope_run_id="scope-1",
    )
    assert changed_target["status"] == "unbound"
    assert changed_target["reason"] == (
        "operator_attested_terminal_target_changed"
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operator_terminal_attestation"]["runtime"]["pid"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    tampered = store.active()
    assert tampered is not None
    assert tampered.operator_terminal_attestation is None
    assert tampered.strategy_snapshot is None


def test_operator_attestation_reuses_matching_independent_strategy_snapshot(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    identity = _identity()
    store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    assert store.record_session_preflight(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    assert store.record_strategy_snapshot(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
    )
    original = store.active()
    assert original is not None
    original_fingerprint = original.strategy_snapshot["fingerprint"]

    assert store.record_operator_terminal_strategy_attestation(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
        runtime_id="runtime-current",
        pid=1234,
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        observation_id="runtime-current:42",
        observed_at=datetime.now(timezone.utc),
        reason="Operator confirmed this retained Game Over is unchanged",
    )
    retained = store.active()
    assert retained is not None
    assert retained.strategy_snapshot["fingerprint"] == original_fingerprint
    assert retained.operator_terminal_attestation[
        "strategy_snapshot_source"
    ] == "independently_durable"


def test_app_accepts_fresh_operator_attestation_without_restart_vector(
    tmp_path,
):
    path = tmp_path / "battle_identity.json"
    store = BattleIdentityStore(path)
    identity = _identity()
    initial, _relation = store.bind(
        identity,
        reason="battle_started",
        operation_id="launch-1",
        acquisition=_acquisition(identity),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("terminal_continuity")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.record_session_preflight(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        configuration_fingerprint="e" * 64,
        evidence={"valid": True, "failed_checks": []},
    )
    assert store.record_operator_terminal_strategy_attestation(
        identity_fingerprint=identity.fingerprint,
        strategy="farm_t19",
        strategy_definition_fingerprint="d" * 64,
        session_preflight_configuration_fingerprint="e" * 64,
        run_configuration={"profile": "farm", "tier": 19},
        runtime_id="runtime-current",
        pid=1234,
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
        observation_id="runtime-current:42",
        observed_at=datetime.now(timezone.utc),
        reason="Operator confirmed this retained Game Over is unchanged",
    )
    app = App.__new__(App)
    app._battle_identity_store = store
    app._retained_battle_identity_record = initial
    app._process_restart_reattachment_enabled = False
    app._mission_mgr = SimpleNamespace(
        awaiting_initial_battle_intent=lambda: True,
    )
    app._supervisor = SimpleNamespace(
        process_restart_handoff={
            "status": "failed",
            "expected_active_round_identity_fingerprint": (
                identity.fingerprint
            ),
            "source_evidence": {
                "game_state": "active_battle",
                "active_round_identity_fingerprint": identity.fingerprint,
                "adb_target": "localhost:5555",
                "target_generation": 3,
            },
        }
    )
    app._current_control_workflow_evidence = Mock(
        return_value={
            "runtime_id": "runtime-current",
            "pid": 1234,
            "adb_target": "localhost:5555",
            "target_generation": 7,
            "game_state": "game_over",
        }
    )

    candidate = app._retained_game_over_binding_candidate(
        "GAME_OVER",
        {"status": "unbound"},
    )

    assert candidate is not None
    assert candidate.fingerprint == identity.fingerprint
    assert candidate.terminal_continuity is None
    assert candidate.operator_terminal_attestation is not None


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


def test_store_rejects_identity_that_is_not_in_the_forced_save(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")

    with pytest.raises(BattleIdentityStoreError):
        store.bind(
            _identity(seed=54321),
            reason="battle_started",
            operation_id="launch-1",
            acquisition=_acquisition(_identity()),
        )


def test_store_rejects_active_save_as_inactive_proof(tmp_path):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")

    with pytest.raises(BattleIdentityStoreError):
        store.mark_inactive(
            reason="home_new_battle",
            operation_id="home-1",
            acquisition=_acquisition(_identity()),
        )


def test_fresh_forced_identity_replaces_corrupt_advisory_state(tmp_path):
    path = tmp_path / "battle_identity.json"
    path.write_text("{not-json", encoding="utf-8")
    store = BattleIdentityStore(path)
    identity = _identity()

    record, relation = store.bind(
        identity,
        reason="automation_resumed",
        operation_id="resume-1",
        acquisition=_acquisition(identity),
    )

    assert relation is BattleIdentityRelation.FIRST_OBSERVATION
    assert record.fingerprint == identity.fingerprint
    assert store.active() == record


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


def test_coordinator_preserves_typed_handoff_continuity_failure(tmp_path):
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="move-1-check",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 8),
    )
    coordinator = _coordinator(tmp_path, context)
    identity = _identity()
    acquisition = _acquisition(identity, target_generation=8)
    serialized = GuardedSerializationResult(
        GuardedSerializationStatus.COMPLETE,
        "save_acquired",
        acquisition=acquisition,
        lifecycle_input_attempted=True,
        background_dispatched=True,
        restoration_completed=True,
    )

    with (
        patch(
            "core.battle_identity.GuardedPlayerSaveSerializer.acquire",
            return_value=serialized,
        ),
        patch.object(
            coordinator._store,
            "bind",
            side_effect=BattleIdentityContinuityError(
                "emulator_handoff_current_wave_regressed"
            ),
        ),
    ):
        result = coordinator.bind(
            context=context,
            action_guard_fn=lambda: True,
            reason="destination_reconciliation",
            initial_frame=object(),
        )

    assert result.status is BattleIdentityCheckStatus.BLOCKED
    assert result.reason == "emulator_handoff_current_wave_regressed"
    assert result.source_restored is True
    assert result.lifecycle_input_attempted is True


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


def test_visual_lifecycle_repairs_a_boundary_hidden_during_pause():
    manager = MissionManager(None, None)
    manager.start()
    assert manager.maybe_run_start({"state": "RUNNING"}) is True
    assert manager.active_battle_observed() is True

    assert manager.observe_active_round_identity(
        _identity().fingerprint,
        changed_from_retained=False,
    ) is False
    assert manager.observe_active_round_identity(
        _identity(seed=98765, counter=10).fingerprint,
        changed_from_retained=True,
    ) is True
    assert manager.active_battle_observed() is False
    assert manager.maybe_run_start({"state": "RUNNING"}) is True


def test_home_new_battle_requires_forced_inactive_save(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    app._emulator_handoff_guard_pending = True
    app._emulator_handoff_guard_warning_logged = True
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )

    bound = app._bind_forced_home_inactive_identity(result)

    assert bound.ready
    assert app._battle_identity_home_verified_preflight_id == "home-check-1"
    assert app._battle_identity_store.active() is None
    assert app._emulator_handoff_guard_pending is False
    assert app._emulator_handoff_guard_warning_logged is False
    app._flag_recoverable_runtime_failure.assert_not_called()


def test_home_new_battle_rejects_forced_active_save(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(_identity()),
    )

    blocked = app._bind_forced_home_inactive_identity(result)

    assert blocked.status is PlayerSavePreflightStatus.BLOCKED
    assert blocked.reason == "home_inactive_round_identity_unavailable"
    assert app._battle_identity_store.active() is None
    app._player_save_preflight_coordinator.discard_carry.assert_called_once()


def test_home_never_adopts_inactive_bytes_from_a_blocked_preflight(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._player_save_preflight_session_id = "home-check-1"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    result = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "workflow_changed_after_restore",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )

    blocked = app._bind_forced_home_inactive_identity(result)

    assert blocked.status is PlayerSavePreflightStatus.BLOCKED
    assert app._battle_identity_store.active() is None
    assert app._battle_identity_home_verified_preflight_id is None


def test_home_start_retries_forced_serialization_then_accepts_inactive_save(
    tmp_path,
):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    unavailable = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "save_acquisition_failed",
        "save_first",
        {},
        {},
        False,
    )
    ready = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.READY,
        "save_reconciled",
        "save_first",
        {},
        {},
        False,
        acquisition=_acquisition(None),
    )
    app._player_save_preflight_coordinator = Mock()
    app._player_save_preflight_coordinator.acquire.side_effect = (
        unavailable,
        ready,
    )
    app._home_battle_identity_attempt_key = Mock(
        return_value=("runtime-1", "start-1", "home_new")
    )
    app._runtime_policy = Mock(return_value={})
    app._flag_recoverable_runtime_failure = Mock()
    app._activity_history_reporter = None
    app._run_perk_selector = Mock()

    first = app._acquire_player_save_home_preflight({}, screenshot=object())
    second = app._acquire_player_save_home_preflight({}, screenshot=object())

    assert not first.ready
    assert second.ready
    assert app._player_save_preflight_coordinator.acquire.call_count == 2
    assert app._battle_identity_home_verified_preflight_id == (
        app._player_save_preflight_session_id
    )
    assert app._battle_identity_home_failed_attempt_key is None
    assert app._battle_identity_home_attempt_count == 0
    app._run_perk_selector.retire.assert_called_once_with(
        "home_inactive_round_proven"
    )


def test_home_start_exhaustion_is_retryable_instead_of_wedging(tmp_path):
    app = App.__new__(App)
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "acknowledged",
    }
    app._supervisor = Mock()
    app._supervisor.battle_workflow = workflow
    app._mission_mgr = Mock()
    unavailable = PlayerSavePreflightResult(
        PlayerSavePreflightStatus.BLOCKED,
        "save_acquisition_failed",
        "save_first",
        {},
        {},
        False,
    )
    app._player_save_preflight_coordinator = Mock()
    app._player_save_preflight_coordinator.acquire.return_value = unavailable
    attempt_key = ("runtime-1", "start-1", "home_new")
    app._home_battle_identity_attempt_key = Mock(return_value=attempt_key)
    app._runtime_policy = Mock(return_value={})
    app._flag_recoverable_runtime_failure = Mock()
    app._current_control_workflow_evidence = Mock(
        return_value={"observation_id": "runtime-1:1"}
    )
    app._activity_history_reporter = None

    first = app._acquire_player_save_home_preflight({}, screenshot=object())
    second = app._acquire_player_save_home_preflight({}, screenshot=object())
    cached = app._acquire_player_save_home_preflight({}, screenshot=object())

    assert first.status is PlayerSavePreflightStatus.BLOCKED
    assert second.status is PlayerSavePreflightStatus.BLOCKED
    assert cached is second
    assert first.reason == "home_inactive_round_identity_unavailable"
    assert app._player_save_preflight_coordinator.acquire.call_count == 2
    assert app._battle_identity_home_failed_attempt_key == attempt_key
    app._mission_mgr.revoke_initial_battle_intent.assert_called_once_with(
        "start_battle",
        request_id="start-1",
    )
    app._supervisor.transition_battle_workflow.assert_called_once()
    assert app._supervisor.transition_battle_workflow.call_args.args[:2] == (
        "start-1",
        "interrupted",
    )
    assert "retry Start" in (
        app._supervisor.transition_battle_workflow.call_args.kwargs["reason"]
    )


def test_running_start_binds_forced_identity_before_completion(tmp_path):
    app = App.__new__(App)
    workflow = {
        "request_id": "start-1",
        "intent": "start_battle",
        "status": "action_dispatched",
    }
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=workflow,
        manual_control=None,
    )
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = False
    app._mission_mgr.observe_active_round_identity.return_value = False
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    identity = _identity()
    acquisition = _acquisition(identity)
    result = SimpleNamespace(
        complete=True,
        identity=identity,
        relation=BattleIdentityRelation.FIRST_OBSERVATION,
        acquisition=acquisition,
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = result
    app._player_save_preflight_coordinator = Mock()
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._control_observation = {
        "game_state": "active_battle",
        "active_battle": True,
    }

    def _assert_identity_precedes_projection(*_args, **_kwargs):
        assert app._control_observation[
            "active_round_identity_fingerprint"
        ] == identity.fingerprint

    app._publish_forced_battle_identity_bundle = Mock(
        side_effect=_assert_identity_precedes_projection
    )
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    consumed = app._force_battle_identity(
        {"state": "RUNNING"},
        object(),
    )

    assert consumed is True
    assert app._active_round_identity_fingerprint == identity.fingerprint
    assert app._battle_identity_reconciliation_required is False
    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=False,
    )
    refresh_running = (
        app._player_save_preflight_coordinator.refresh_running_evidence
    )
    refresh_running.assert_called_once_with(
        acquisition,
        active_round_identity_fingerprint=identity.fingerprint,
    )
    app._publish_forced_battle_identity_bundle.assert_called_once_with(
        acquisition,
        relation=BattleIdentityRelation.FIRST_OBSERVATION,
        identity_fingerprint=identity.fingerprint,
    )


def test_verified_target_handoff_is_forwarded_to_save_projectors():
    app = App.__new__(App)
    identity = _identity()
    acquisition = _acquisition(identity, target_generation=8)
    app._player_save_runtime_session_id = "runtime-1"
    app._current_run_scope_id = Mock(return_value="scope-1")
    app._publish_player_save_observation = Mock()
    app._supervisor = SimpleNamespace(
        battle_workflow=None,
        manual_control=None,
    )

    app._publish_forced_battle_identity_bundle(
        acquisition,
        relation=BattleIdentityRelation.SAME_BATTLE,
        identity_fingerprint=identity.fingerprint,
        verified_target_handoff=True,
    )

    app._publish_player_save_observation.assert_called_once()
    call = app._publish_player_save_observation.call_args
    assert call.args == (acquisition,)
    assert call.kwargs["context"].target_binding == acquisition.binding
    assert call.kwargs["reason_code"] == "forced_battle_identity"
    assert call.kwargs["bind_new_activity"] is False
    assert call.kwargs["verified_target_handoff"] is True


def test_verified_same_battle_handoff_rebinds_destination_perk_bundle():
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = None
    app._supervisor.manual_control = None
    app._supervisor.manual_control_error = False
    app._supervisor.battle_workflow_error = False
    app._supervisor.setup_capture_error = False
    app._supervisor.setup_capture = None
    app._awaiting_initial_battle_intent = Mock(return_value=False)
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._mission_mgr.observe_active_round_identity.return_value = False
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=8,
            owned=True,
        )
    )
    identity = _identity()
    acquisition = _acquisition(identity, target_generation=8)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=BattleIdentityRelation.SAME_BATTLE,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = Mock()
    app._battle_identity_store.active.return_value = SimpleNamespace(
        emulator_handoff_guard=None,
    )
    app._player_save_preflight_coordinator = Mock()
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._control_observation = {
        "game_state": "active_battle",
        "active_battle": True,
    }
    app._observed_active_round_identity_fingerprint = identity.fingerprint
    app._active_round_identity_fingerprint = identity.fingerprint
    app._battle_identity_reconciliation_required = True
    app._emulator_handoff_guard_pending = True
    app._emulator_handoff_guard_warning_logged = True

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    app._publish_forced_battle_identity_bundle.assert_called_once_with(
        acquisition,
        relation=BattleIdentityRelation.SAME_BATTLE,
        identity_fingerprint=identity.fingerprint,
        verified_target_handoff=True,
    )
    assert app._emulator_handoff_guard_pending is False


def test_home_resume_dispatch_makes_identity_non_authoritative_until_running():
    app = App.__new__(App)
    retained = object()
    observed = "a" * 64
    app._retained_battle_identity_record = retained
    app._observed_active_round_identity_fingerprint = observed
    app._active_round_identity = _identity()
    app._active_round_identity_fingerprint = observed
    app._terminal_round_identity_fingerprint = observed
    app._battle_identity_reconciliation_required = False
    app._battle_identity_operation_id = "return-1"
    app._battle_identity_operation_kind = "manual_return"
    app._battle_identity_failed_attempt_key = ("failed",)
    app._battle_identity_attempt_key = ("attempt",)
    app._battle_identity_attempt_count = 1
    app._battle_identity_retry_after = 42.0

    app._rearm_battle_identity_after_home_resume_dispatch()

    assert app._retained_battle_identity_record is retained
    assert app._observed_active_round_identity_fingerprint == observed
    assert app._active_round_identity is None
    assert app._active_round_identity_fingerprint is None
    assert app._terminal_round_identity_fingerprint is None
    assert app._battle_identity_reconciliation_required is True
    assert app._battle_identity_operation_id is None
    assert app._battle_identity_operation_kind is None
    assert app._battle_identity_failed_attempt_key is None
    assert app._battle_identity_attempt_key is None
    assert app._battle_identity_attempt_count == 0
    assert app._battle_identity_retry_after == 0.0


def test_verified_home_resume_dispatch_rearms_forced_running_identity():
    frame = object()
    app = App.__new__(App)
    app._operator_battle_intent_required = True
    app._auto_start_enabled = True
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._mission_mgr = Mock()
    app._mission_mgr.awaiting_initial_battle_intent.return_value = False
    app._mission_mgr.no_battle_setup_requirements.return_value = {}
    app._supervisor = Mock()
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._supervisor.emulator_maintenance = None
    app._handle_home_return_reconciliation = Mock(return_value=False)
    app._handler_enabled = Mock(side_effect=lambda name: name == "home")
    app._exclusive_validation_definition = Mock(return_value=None)
    app._maybe_start_exclusive_validation = Mock(return_value=False)
    app._report_home_policy = Mock()
    app._player_save_preflight_coordinator = None
    app._current_control_workflow_evidence = Mock(
        return_value={"observation_id": "runtime-1:home"}
    )
    app._home_launch_authority_matches = Mock(return_value=True)
    app._mark_operator_battle_action_dispatched = Mock(return_value=True)
    app._rearm_battle_identity_after_home_resume_dispatch = Mock()

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.RESUME_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch(
            "core.app.handle_home_screen",
            return_value=TapDispatchOutcome(
                TapDispatchStatus.DISPATCHED
            ),
        ) as handle_home,
    ):
        app._handle_primary_states(
            "HOME_SCREEN",
            set(),
            frame,
            operator_workflow_only=True,
        )

    handle_home.assert_called_once()
    assert handle_home.call_args.kwargs["require_resume_battle"] is True
    app._mark_operator_battle_action_dispatched.assert_called_once_with(True)
    app._rearm_battle_identity_after_home_resume_dispatch.assert_called_once_with()


def test_running_changed_identity_repairs_a_boundary_hidden_during_pause(
    tmp_path,
):
    app = App.__new__(App)
    workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=workflow,
        manual_control=None,
    )
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._mission_mgr.observe_active_round_identity.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    identity = _identity(seed=98765, counter=10)
    acquisition = _acquisition(identity)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=BattleIdentityRelation.LATER_BATTLE,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._run_perk_selector = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    with patch("core.app.start_activity_scope") as rotate_scope:
        assert app._force_battle_identity(
            {"state": "RUNNING"},
            object(),
        )

    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=True,
    )
    rotate_scope.assert_called_once()
    app._run_perk_selector.retire.assert_called_once_with(
        "active_round_identity_changed"
    )
    assert app._active_round_identity_fingerprint == identity.fingerprint


def test_terminal_home_continuation_owns_successor_forced_identity(tmp_path):
    app = App.__new__(App)
    operation_id = "terminal-launch-1"
    app._supervisor = SimpleNamespace(
        is_paused=False,
        battle_workflow=None,
        manual_control=None,
    )
    app._terminal_home_continuation = {
        "operation_id": operation_id,
        "phase": "action_dispatched",
    }
    app._terminal_home_continuation_owner_current = Mock(return_value=True)
    app._awaiting_initial_battle_intent = Mock(return_value=False)
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = False
    app._mission_mgr.observe_active_round_identity.return_value = False
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    identity = _identity(seed=98765, counter=10)
    acquisition = _acquisition(identity)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.COMPLETE,
            "active_round_identity_bound",
            identity=identity,
            relation=BattleIdentityRelation.FIRST_OBSERVATION,
            acquisition=acquisition,
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._publish_forced_battle_identity_bundle = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    context = app._battle_identity_coordinator.bind.call_args.kwargs[
        "context"
    ]
    assert context.operation_id == operation_id
    assert app._active_round_identity_fingerprint == identity.fingerprint
    app._mission_mgr.observe_active_round_identity.assert_called_once_with(
        identity.fingerprint,
        changed_from_retained=False,
    )


def test_runtime_identity_retries_by_forced_serialization_after_cooldown(
    tmp_path,
):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    context = BattleIdentityCheckContext(
        runtime_session_id="runtime-1",
        operation_id="runtime-check",
        target_binding=PlayerSaveTargetBinding("localhost:5555", 7),
    )
    app._battle_identity_check_owner = Mock(
        return_value=(
            "runtime-check",
            "runtime_reconciliation",
            AuthorityHold.BATTLE_IDENTITY,
            "runtime_reconciliation",
        )
    )
    app._current_battle_identity_check_context = Mock(return_value=context)
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.UNAVAILABLE,
            "save_acquisition_failed",
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = None

    now = [100.0]
    with patch("core.app.time.monotonic", side_effect=lambda: now[0]):
        assert app._force_battle_identity({"state": "RUNNING"}, object())
        assert app._force_battle_identity({"state": "RUNNING"}, object())
        now[0] = 129.0
        assert not app._force_battle_identity({"state": "RUNNING"}, object())
        now[0] = 131.0
        assert app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._battle_identity_coordinator.bind.call_count == 3


def test_safe_identity_failure_never_retains_stale_authority(tmp_path):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.UNAVAILABLE,
            "save_acquisition_failed",
            source_restored=True,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    assert app._force_battle_identity({"state": "RUNNING"}, object())
    assert app._force_battle_identity({"state": "RUNNING"}, object())
    assert not app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._active_round_identity_fingerprint is None
    assert app._battle_identity_reconciliation_required is True
    assert app._battle_identity_coordinator.bind.call_count == 2
    assert app._flag_recoverable_runtime_failure.call_count == 2
    app._supervisor.pause_for_catastrophic_failure.assert_not_called()
    app._mission_mgr.revoke_initial_battle_intent.assert_called_once_with(
        "attach_battle",
        request_id="attach-1",
    )
    app._supervisor.transition_battle_workflow.assert_called_once()
    assert (
        app._supervisor.transition_battle_workflow.call_args.args[:2]
        == ("attach-1", "interrupted")
    )


def test_unrestored_identity_serialization_pauses_before_any_battle_work(
    tmp_path,
):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.BLOCKED,
            "source_restoration_failed",
            source_restored=False,
            lifecycle_input_attempted=True,
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True
    app._active_round_identity_fingerprint = _identity().fingerprint

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    assert app._active_round_identity_fingerprint is None
    app._supervisor.pause_for_catastrophic_failure.assert_called_once()
    app._flag_recoverable_runtime_failure.assert_not_called()


@pytest.mark.parametrize(
    ("reason", "expected_kind"),
    [
        ("exact_target_ownership_unverified", "target_ownership_lost"),
        ("restored_target_binding_unverified", "target_ownership_lost"),
        (
            "emulator_handoff_current_wave_regressed",
            "save_continuity_lost",
        ),
    ],
)
def test_unsafe_identity_continuity_loss_is_catastrophic(
    reason,
    expected_kind,
    tmp_path,
):
    app = App.__new__(App)
    app._supervisor = Mock(is_paused=False)
    app._supervisor.battle_workflow = {
        "request_id": "attach-1",
        "intent": "attach_battle",
        "status": "validating_save",
    }
    app._supervisor.manual_control = None
    app._mission_mgr = Mock()
    app._mission_mgr.active_battle_observed.return_value = True
    app._player_save_runtime_session_id = "runtime-1"
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            target="localhost:5555",
            generation=7,
            owned=True,
        )
    )
    app._battle_identity_coordinator = Mock()
    app._battle_identity_coordinator.bind.return_value = (
        BattleIdentityCheckResult(
            BattleIdentityCheckStatus.BLOCKED,
            reason,
            source_restored=(
                reason.startswith("restored_")
                or reason.startswith("emulator_handoff_")
            ),
            lifecycle_input_attempted=(
                reason.startswith("restored_")
                or reason.startswith("emulator_handoff_")
            ),
        )
    )
    app._battle_identity_store = BattleIdentityStore(
        tmp_path / "battle_identity.json"
    )
    app._update_action_authority = Mock()
    app._runtime_action_guard = Mock(return_value=True)
    app._flag_recoverable_runtime_failure = Mock()
    app._battle_identity_reconciliation_required = True

    assert app._force_battle_identity({"state": "RUNNING"}, object())

    app._supervisor.pause_for_catastrophic_failure.assert_called_once()
    kind = app._supervisor.pause_for_catastrophic_failure.call_args.args[0]
    assert kind.value == expected_kind
    app._flag_recoverable_runtime_failure.assert_not_called()


def test_pause_and_enable_revoke_identity_until_forced_revalidation():
    app = App.__new__(App)
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.RUNNING
        app._battle_identity_last_control_state = "RUNNING"
        app._active_round_identity = _identity()
        app._active_round_identity_fingerprint = _identity().fingerprint
        app._terminal_round_identity_fingerprint = _identity().fingerprint
        app._battle_identity_reconciliation_required = False
        app._battle_identity_home_verified_preflight_id = "home-1"
        app._battle_identity_operation_id = "operation-1"
        app._battle_identity_operation_kind = "runtime_reconciliation"
        app._battle_identity_failed_attempt_key = (
            "runtime_reconciliation",
            "runtime-1",
            "binding",
            "RUNNING",
        )

        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()

        assert app._active_round_identity is None
        assert app._active_round_identity_fingerprint is None
        assert app._terminal_round_identity_fingerprint is None
        assert app._battle_identity_reconciliation_required is True
        assert app._battle_identity_home_verified_preflight_id is None
        assert app._battle_identity_failed_attempt_key is None

        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()
        assert app._battle_identity_reconciliation_required is True
    finally:
        AUTOMATION.state = prior_state


def test_pause_surrender_home_enable_then_start_uses_forced_save_boundaries(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    old_identity = _identity()
    store.bind(
        old_identity,
        reason="battle_started",
        operation_id="old-launch",
        acquisition=_acquisition(old_identity),
    )
    app = App.__new__(App)
    app._battle_identity_store = store
    app._battle_identity_last_control_state = "RUNNING"
    app._active_round_identity = old_identity
    app._active_round_identity_fingerprint = old_identity.fingerprint
    app._terminal_round_identity_fingerprint = old_identity.fingerprint
    app._battle_identity_reconciliation_required = False
    app._player_save_preflight_session_id = "home-after-surrender"
    app._player_save_preflight_coordinator = Mock()
    app._flag_recoverable_runtime_failure = Mock()
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()
        assert app._active_round_identity_fingerprint is None
        assert store.active().fingerprint == old_identity.fingerprint

        home_result = PlayerSavePreflightResult(
            PlayerSavePreflightStatus.READY,
            "save_reconciled",
            "save_first",
            {},
            {},
            False,
            acquisition=_acquisition(None),
        )
        assert app._bind_forced_home_inactive_identity(home_result).ready
        assert store.active() is None

        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()
        assert app._battle_identity_reconciliation_required is True

        new_identity = _identity(seed=54321, counter=10)
        acquisition = _acquisition(new_identity)
        app._supervisor = SimpleNamespace(
            is_paused=False,
            battle_workflow={
                "request_id": "new-launch",
                "intent": "start_battle",
                "status": "action_dispatched",
            },
            manual_control=None,
        )
        app._mission_mgr = Mock()
        app._mission_mgr.active_battle_observed.return_value = False
        app._mission_mgr.observe_active_round_identity.return_value = False
        app._player_save_runtime_session_id = "runtime-1"
        app._adb_target_session = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                target="localhost:5555",
                generation=7,
                owned=True,
            )
        )

        def _bind_successor(**kwargs):
            record, relation = store.bind(
                new_identity,
                reason="battle_started",
                operation_id=kwargs["context"].operation_id,
                acquisition=acquisition,
            )
            assert record.fingerprint == new_identity.fingerprint
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.COMPLETE,
                "active_round_identity_bound",
                identity=new_identity,
                relation=relation,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )

        app._battle_identity_coordinator = Mock()
        app._battle_identity_coordinator.bind.side_effect = _bind_successor
        app._update_action_authority = Mock()
        app._runtime_action_guard = Mock(return_value=True)
        app._publish_forced_battle_identity_bundle = Mock()

        assert app._force_battle_identity({"state": "RUNNING"}, object())
        assert store.active().fingerprint == new_identity.fingerprint
        assert app._active_round_identity_fingerprint == (
            new_identity.fingerprint
        )
        app._mission_mgr.observe_active_round_identity.assert_called_once_with(
            new_identity.fingerprint,
            changed_from_retained=False,
        )
    finally:
        AUTOMATION.state = prior_state


def test_pause_hidden_manual_successor_enable_classifies_later_save_identity(
    tmp_path,
):
    store = BattleIdentityStore(tmp_path / "battle_identity.json")
    old_identity = _identity()
    store.bind(
        old_identity,
        reason="battle_started",
        operation_id="old-launch",
        acquisition=_acquisition(old_identity),
    )
    app = App.__new__(App)
    app._battle_identity_last_control_state = "RUNNING"
    app._active_round_identity = old_identity
    app._active_round_identity_fingerprint = old_identity.fingerprint
    app._terminal_round_identity_fingerprint = old_identity.fingerprint
    app._battle_identity_reconciliation_required = False
    app._battle_identity_store = store
    prior_state = AUTOMATION.state
    try:
        AUTOMATION.state = RunState.PAUSED
        app._sync_battle_identity_control_boundary()
        AUTOMATION.state = RunState.RUNNING
        app._sync_battle_identity_control_boundary()

        successor = _identity(seed=98765, counter=10)
        acquisition = _acquisition(successor)
        app._supervisor = SimpleNamespace(
            is_paused=False,
            battle_workflow=None,
            manual_control={
                "manual_control_id": "resume-after-manual-start",
                "status": "reconciling",
            },
        )
        app._mission_mgr = Mock()
        app._mission_mgr.active_battle_observed.return_value = True
        app._mission_mgr.observe_active_round_identity.return_value = True
        app._player_save_runtime_session_id = "runtime-1"
        app._adb_target_session = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                target="localhost:5555",
                generation=7,
                owned=True,
            )
        )

        def _bind_successor(**kwargs):
            record, relation = store.bind(
                successor,
                reason="automation_resumed",
                operation_id=kwargs["context"].operation_id,
                acquisition=acquisition,
            )
            return BattleIdentityCheckResult(
                BattleIdentityCheckStatus.COMPLETE,
                "active_round_identity_bound",
                identity=record.identity,
                relation=relation,
                acquisition=acquisition,
                source_restored=True,
                lifecycle_input_attempted=True,
            )

        app._battle_identity_coordinator = Mock()
        app._battle_identity_coordinator.bind.side_effect = _bind_successor
        app._update_action_authority = Mock()
        app._runtime_action_guard = Mock(return_value=True)
        app._publish_forced_battle_identity_bundle = Mock()

        with patch("core.app.start_activity_scope", return_value=None):
            assert app._force_battle_identity(
                {"state": "RUNNING"},
                object(),
            )

        record = store.active()
        assert record.fingerprint == successor.fingerprint
        assert record.session_preflight is None
        assert app._active_round_identity_fingerprint == successor.fingerprint
        app._mission_mgr.observe_active_round_identity.assert_called_once_with(
            successor.fingerprint,
            changed_from_retained=True,
        )
    finally:
        AUTOMATION.state = prior_state


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
