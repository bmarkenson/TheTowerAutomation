from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.player_save import PlayerSaveSnapshot, SaveCheckEvidence
from core.player_save import pull_player_save_bytes
from core.player_save_confirmed_local_mapping import ConfirmedLocalMappingStore
from core.player_save_mapping_candidates import (
    AppendOnlyMappingCandidateStore,
    build_mapping_candidate_ui_evidence,
    pending_mapping_candidate,
)
from core.player_save_preflight import (
    CarriedEvidenceState,
    PlayerSavePreflightContext,
    PlayerSavePreflightCoordinator,
    PlayerSavePreflightStatus,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)


CAPTURED_AT = datetime(2026, 8, 4, tzinfo=timezone.utc).isoformat()


def _snapshot() -> PlayerSaveSnapshot:
    checks = {
        "cards_deck": SaveCheckEvidence(
            "cards_deck",
            "observed",
            "Farm",
            ("presetName", "currentPreset"),
            authority={"kind": "matching_value"},
        ),
        "auto_pick_perks": SaveCheckEvidence(
            "auto_pick_perks",
            "observed",
            True,
            ("autoPickPerk",),
            authority={"kind": "allowed_values", "values": [True]},
        ),
    }
    return PlayerSaveSnapshot(
        captured_at=CAPTURED_AT,
        source_name="playerInfo.dat",
        source_sha256="a" * 64,
        source_size=123,
        container="gzip+nrbf",
        decompressed_size=456,
        root_class="SaveLoad+PlayerData",
        field_count=100,
        data_version=9,
        game_version=1073,
        save_revision=55,
        mapping_id="data-9-game-1073",
        mapping_maturity="candidate",
        validated_checks=("cards_deck", "auto_pick_perks"),
        shape_valid=True,
        warnings=(),
        profile_summary={},
        checks=checks,
        runtime_save=None,
    )


def _snapshot_with_history(
    *,
    semantic_status: str = "observed",
) -> PlayerSaveSnapshot:
    identity = SimpleNamespace(
        mapping_id="data-9-game-1073",
        fingerprint="b" * 64,
        tier=19,
        wave=1899,
        battle_date={
            "kind_id": 2,
            "kind": "local",
            "ticks": "639197340971234560",
            "clock_time": "2026-07-15T01:41:37.123456",
            "clock_basis": "local_wall_clock_without_offset",
            "submicrosecond_100ns": 0,
        },
    )
    tail = SimpleNamespace(
        structural_status="observed",
        structural_reason="",
        identity=identity,
        entry_count=30,
        capacity=30,
        completed_entry_status=semantic_status,
        completed_entry_reason=(
            "unmapped_killed_by_id:999"
            if semantic_status == "unavailable"
            else ""
        ),
    )
    runtime = SimpleNamespace(
        mapping_id="data-9-game-1073",
        battle_history_tail=tail,
    )
    return replace(_snapshot(), runtime_save=runtime)


def _context(*, generation: int = 1, strategy: str = "farm_t19"):
    return PlayerSavePreflightContext(
        runtime_session_id="runtime-private",
        preflight_session_id="preflight-private",
        activity_scope_id="activity-private",
        strategy_name=strategy,
        configuration_fingerprint="f" * 64,
        target="private-device-target",
        target_generation=generation,
    )


def _terminal_acquisition(
    *,
    source_scope: str = "activity-source",
    runtime_session: str = "runtime-private",
    target: str = "private-device-target",
    generation: int = 1,
    kind: PlayerSaveBoundaryKind = PlayerSaveBoundaryKind.GAME_OVER,
) -> PlayerSaveAcquisitionBundle:
    captured = datetime.fromisoformat(CAPTURED_AT)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.NATURAL_BOUNDARY,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="stable_natural_boundary",
        binding=PlayerSaveTargetBinding(target, generation),
        acquisition_started_at=captured - timedelta(milliseconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=_snapshot(),
        boundary=PlayerSaveNaturalBoundary(
            kind=kind,
            observed_at=captured,
            runtime_session_id=runtime_session,
            activity_scope_id=source_scope,
        ),
    )


def _coordinator(
    monkeypatch,
    *,
    context_fn=lambda: _context(),
    target_snapshot_fn=lambda: AdbTargetSnapshot(
        "private-device-target", 1, True
    ),
    pull_fn=lambda **_kwargs: b"stable-save",
    decode_fn=lambda _payload, **_kwargs: _snapshot(),
    background_fn=lambda _target: True,
    foreground_fn=lambda _target: True,
    capture_fn=lambda: object(),
    action_guard_fn=lambda: True,
    mapping_candidate_store=None,
    confirmed_local_mapping_store=None,
):
    import core.player_save_preflight as module

    monkeypatch.setattr(module, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "log_action_intent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(module, "log_input", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "log_result", lambda *_args, **_kwargs: None)
    return PlayerSavePreflightCoordinator(
        target_snapshot_fn=target_snapshot_fn,
        context_fn=context_fn,
        action_guard_fn=action_guard_fn,
        capture_fn=capture_fn,
        detector=lambda _frame: {"state": "HOME_SCREEN"},
        home_control_fn=lambda _frame: SimpleNamespace(
            control=HomeBattleControl.NEW_BATTLE
        ),
        background_fn=background_fn,
        foreground_fn=foreground_fn,
        pull_fn=pull_fn,
        decode_fn=decode_fn,
        mapping_candidate_store=mapping_candidate_store,
        confirmed_local_mapping_store=confirmed_local_mapping_store,
        sleep_fn=lambda _seconds: None,
    )


MODULE_ASSIGNMENTS = {
    "cannon_primary": "Amplifying Strike",
    "armor_primary": "Orbital Augment",
    "generator_primary": "Black Hole Digestor",
    "core_primary": "Multiverse Nexus",
    "cannon_assist": "Being Annihilator",
    "armor_assist": "Anti-Cube Portal",
    "generator_assist": "Singularity Harness",
    "core_assist": "Magnetic Hook",
}


def _snapshot_with_module_mapping_candidate() -> PlayerSaveSnapshot:
    candidate = pending_mapping_candidate(
        value_kind="module_info_index",
        raw_value=777,
        pairing_method="exact_locator",
        locator="core_assist",
        expected_observation_count=8,
        known_semantic_values=("Dimension Core", "Harmony Conductor"),
        peer_locator_values={
            slot_key: name
            for slot_key, name in MODULE_ASSIGNMENTS.items()
            if slot_key != "core_assist"
        },
        scope={
            "slot_key": "core_assist",
            "family": "core",
            "role": "assist",
        },
    )
    evidence = SaveCheckEvidence(
        "modules",
        "unmapped",
        None,
        ("moduleEquipped", "assistModuleSlots"),
        complete=False,
        reason="unsupported assist module infoIndex",
        diagnostics={"mapping_candidates": [candidate]},
    )
    snapshot = _snapshot()
    return replace(
        snapshot,
        mapping_authority_id=snapshot.mapping_id,
        mapping_structural_id=snapshot.mapping_id,
        mapping_semantic_fingerprint="b" * 64,
        validated_checks=(*snapshot.validated_checks, "modules"),
        checks={**snapshot.checks, "modules": evidence},
    )


def _module_mapping_ui_evidence(*, pre_mutation: bool = True):
    scopes = {
        slot_key: {
            "slot_key": slot_key,
            "family": slot_key.removesuffix("_primary").removesuffix(
                "_assist"
            ),
            "role": "primary" if slot_key.endswith("_primary") else "assist",
        }
        for slot_key in MODULE_ASSIGNMENTS
    }
    return build_mapping_candidate_ui_evidence(
        "modules",
        canonical_values=list(MODULE_ASSIGNMENTS.values()),
        locator_values=MODULE_ASSIGNMENTS,
        locator_scopes=scopes,
        pre_mutation=pre_mutation,
    )


def test_candidate_receipt_and_local_confirmation_preserve_current_ui_fallback(
    monkeypatch,
    tmp_path,
):
    candidate_store = AppendOnlyMappingCandidateStore(
        tmp_path / "candidate-receipts.jsonl"
    )
    confirmation_store = ConfirmedLocalMappingStore(tmp_path / "local")
    coordinator = _coordinator(
        monkeypatch,
        decode_fn=lambda _payload, **_kwargs: (
            _snapshot_with_module_mapping_candidate()
        ),
        mapping_candidate_store=candidate_store,
        confirmed_local_mapping_store=confirmation_store,
    )

    result = coordinator.acquire(
        {"modules": MODULE_ASSIGNMENTS},
        initial_frame=object(),
    )
    recorded = coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(),
    )

    assert result.decisions["modules"]["disposition"] == "ui_required"
    assert result.decisions["modules"]["ui_required"] is True
    assert result.decisions["modules"]["observed"] is None
    assert recorded == 1
    assert coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(),
    ) == 0
    records = candidate_store.list_records()
    assert len(records) == 1
    assert records[0]["candidate"] == {
        "check_id": "modules",
        "value_kind": "module_info_index",
        "raw_discriminator": {"kind": "integer_id", "value": 777},
        "locator": "core_assist",
        "scope": {
            "slot_key": "core_assist",
            "family": "core",
            "role": "assist",
        },
        "semantic_value": "Magnetic Hook",
        "observed_semantic_values": list(MODULE_ASSIGNMENTS.values()),
        "status": "ready_for_review",
        "reason": (
            "unique exact-boundary pre-mutation pairing is ready for "
            "operator review"
        ),
    }
    document = confirmation_store.load(9, 1073)
    assert document is not None
    assert document["generation"] == 1
    assert document["events"][0]["semantic_value"] == "Magnetic Hook"
    assert document["events"][0]["raw_value"] == 777


def test_candidate_correlation_requires_pre_mutation_and_same_context(
    monkeypatch,
    tmp_path,
):
    current = [_context()]
    candidate_store = AppendOnlyMappingCandidateStore(
        tmp_path / "candidate-receipts.jsonl"
    )
    coordinator = _coordinator(
        monkeypatch,
        context_fn=lambda: current[0],
        decode_fn=lambda _payload, **_kwargs: (
            _snapshot_with_module_mapping_candidate()
        ),
        mapping_candidate_store=candidate_store,
        confirmed_local_mapping_store=ConfirmedLocalMappingStore(
            tmp_path / "local"
        ),
    )
    coordinator.acquire(
        {"modules": MODULE_ASSIGNMENTS},
        initial_frame=object(),
    )

    assert coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(pre_mutation=False),
    ) == 0
    current[0] = replace(_context(), target_generation=2)
    assert coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(),
    ) == 0
    assert candidate_store.list_records() == []


def test_ui_repair_closes_candidate_window_without_invalidating_snapshot(
    monkeypatch,
    tmp_path,
):
    candidate_store = AppendOnlyMappingCandidateStore(
        tmp_path / "candidate-receipts.jsonl"
    )
    coordinator = _coordinator(
        monkeypatch,
        decode_fn=lambda _payload, **_kwargs: (
            _snapshot_with_module_mapping_candidate()
        ),
        mapping_candidate_store=candidate_store,
        confirmed_local_mapping_store=ConfirmedLocalMappingStore(
            tmp_path / "local"
        ),
    )
    coordinator.acquire(
        {"modules": MODULE_ASSIGNMENTS},
        initial_frame=object(),
    )

    assert coordinator.record_ui_verification("cards_deck", changed=True)
    assert not coordinator.snapshot_invalidated
    assert coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(),
    ) == 0
    assert candidate_store.list_records() == []


def test_candidate_write_failure_is_nonblocking(monkeypatch, tmp_path):
    candidate_store = Mock()
    candidate_store.append_once.side_effect = OSError("candidate disk failed")
    confirmation_store = Mock()
    coordinator = _coordinator(
        monkeypatch,
        decode_fn=lambda _payload, **_kwargs: (
            _snapshot_with_module_mapping_candidate()
        ),
        mapping_candidate_store=candidate_store,
        confirmed_local_mapping_store=confirmation_store,
    )
    result = coordinator.acquire(
        {"modules": MODULE_ASSIGNMENTS},
        initial_frame=object(),
    )

    assert coordinator.record_mapping_observation(
        "modules",
        _module_mapping_ui_evidence(),
    ) == 0
    assert result.decisions["modules"]["ui_required"] is True
    assert coordinator.mapping_candidate_records == ()
    confirmation_store.accept_candidate.assert_not_called()


def test_one_authoritative_snapshot_reconciles_all_checks(monkeypatch):
    calls = {"pull": 0, "decode": 0, "background": 0, "foreground": 0}

    def pull(**_kwargs):
        calls["pull"] += 1
        return b"stable-save"

    def decode(_payload, **_kwargs):
        calls["decode"] += 1
        return _snapshot()

    coordinator = _coordinator(
        monkeypatch,
        pull_fn=pull,
        decode_fn=decode,
        background_fn=lambda _target: (
            calls.__setitem__("background", calls["background"] + 1) or True
        ),
        foreground_fn=lambda _target: (
            calls.__setitem__("foreground", calls["foreground"] + 1) or True
        ),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm", "auto_pick_perks": True},
        initial_frame=object(),
    )

    assert result.status is PlayerSavePreflightStatus.READY
    assert result.accepted_checks == ("auto_pick_perks", "cards_deck")
    assert calls == {
        "pull": 1,
        "decode": 1,
        "background": 1,
        "foreground": 1,
    }
    assert result.carry is coordinator.carry
    assert not hasattr(result, "snapshot")
    rendered = json.dumps(result.as_dict())
    assert "private-device-target" not in rendered
    assert "runtime-private" not in rendered
    assert "stable-save" not in rendered
    assert "a" * 64 not in rendered
    assert result.provenance["save_version"] == {"data": 9, "game": 1073}


def test_natural_game_over_save_binds_only_to_exact_retry_successor(monkeypatch):
    successor = replace(_context(), activity_scope_id="activity-retry")
    coordinator = _coordinator(monkeypatch, context_fn=lambda: successor)

    result = coordinator.stage_direct_retry(
        _terminal_acquisition(),
        {"cards_deck": "Farm", "auto_pick_perks": True},
        source_activity_scope_id="activity-source",
    )

    assert result.ready
    assert result.reason == "direct_retry_save_reconciled"
    assert result.accepted_checks == ("auto_pick_perks", "cards_deck")
    assert result.carry is coordinator.carry
    assert result.carry is not None
    assert result.carry.state is CarriedEvidenceState.LAUNCH_DISPATCHED
    assert result.carry.launch_kind == "game_over_direct_retry"
    assert not coordinator.bind_running(
        battle_started=True,
        stable_running=False,
        continuity_verified=True,
    )
    assert coordinator.bind_running(
        battle_started=False,
        stable_running=True,
        continuity_verified=True,
    )
    assert coordinator.consume("auto_pick_perks") is True


@pytest.mark.parametrize(
    "acquisition",
    [
        _terminal_acquisition(runtime_session="different-runtime"),
        _terminal_acquisition(source_scope="different-source"),
        _terminal_acquisition(target="different-target"),
        _terminal_acquisition(generation=2),
        _terminal_acquisition(kind=PlayerSaveBoundaryKind.TOURNAMENT_RESULTS),
    ],
)
def test_direct_retry_binding_change_uses_ui_without_blocking_route(
    monkeypatch,
    acquisition,
):
    successor = replace(_context(), activity_scope_id="activity-retry")
    coordinator = _coordinator(monkeypatch, context_fn=lambda: successor)

    result = coordinator.stage_direct_retry(
        acquisition,
        {"cards_deck": "Farm", "auto_pick_perks": True},
        source_activity_scope_id="activity-source",
    )

    assert result.ready
    assert result.reason == "direct_retry_binding_unverified"
    assert result.carry is None
    assert result.ui_required_checks == ("auto_pick_perks", "cards_deck")


def test_direct_retry_requires_a_distinct_successor_scope(monkeypatch):
    same_scope = replace(_context(), activity_scope_id="activity-source")
    coordinator = _coordinator(monkeypatch, context_fn=lambda: same_scope)

    result = coordinator.stage_direct_retry(
        _terminal_acquisition(),
        {"cards_deck": "Farm", "auto_pick_perks": True},
        source_activity_scope_id="activity-source",
    )

    assert result.ready
    assert result.reason == "direct_retry_binding_unverified"
    assert result.carry is None
    assert result.ui_required_checks == ("auto_pick_perks", "cards_deck")


def test_direct_retry_active_round_projection_uses_ui_without_blocking_route(
    monkeypatch,
):
    acquisition = _terminal_acquisition()
    active_runtime = SimpleNamespace(round_active=True)
    acquisition = replace(
        acquisition,
        snapshot=replace(acquisition.snapshot, runtime_save=active_runtime),
    )
    successor = replace(_context(), activity_scope_id="activity-retry")
    coordinator = _coordinator(monkeypatch, context_fn=lambda: successor)

    result = coordinator.stage_direct_retry(
        acquisition,
        {"cards_deck": "Farm", "auto_pick_perks": True},
        source_activity_scope_id="activity-source",
    )

    assert result.ready
    assert result.reason == "direct_retry_binding_unverified"
    assert result.carry is None
    assert result.ui_required_checks == ("auto_pick_perks", "cards_deck")


def test_requirement_fallback_preserves_unrelated_carried_check(monkeypatch):
    coordinator = _coordinator(monkeypatch)
    carry = coordinator.acquire(
        {"cards_deck": "Farm", "auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None
    coordinator.fallback_checks(
        "cards_requirement_changed",
        check_ids=("cards_deck",),
    )
    assert carry.values == {"auto_pick_perks": True}
    assert coordinator.decision("cards_deck")["disposition"] == "ui_required"
    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    assert coordinator.bind_running(
        battle_started=True,
        stable_running=True,
        continuity_verified=True,
    )
    assert coordinator.consume("auto_pick_perks") is True


def test_trusted_mismatch_queues_ui_without_erasing_unrelated_carry(monkeypatch):
    coordinator = _coordinator(monkeypatch)

    result = coordinator.acquire(
        {"cards_deck": "Tournament", "auto_pick_perks": True},
        initial_frame=object(),
    )

    assert result.accepted_checks == ("auto_pick_perks",)
    assert result.trusted_mismatch_checks == ("cards_deck",)
    assert result.decisions["cards_deck"]["disposition"] == "save_mismatch"
    assert result.decisions["cards_deck"]["repair_queued"] is True
    assert result.carry is not None
    assert result.carry.values == {"auto_pick_perks": True}

    assert coordinator.record_ui_verification("cards_deck", changed=True)
    assert coordinator.record_ui_verification("cards_deck", changed=False)
    assert coordinator.ui_verified_checks == {
        "cards_deck": "ui_verified_repair"
    }
    assert result.carry.values == {"auto_pick_perks": True}
    assert "cards_deck" not in result.carry.values

    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    assert coordinator.bind_running(
        battle_started=True,
        stable_running=True,
        continuity_verified=True,
    )
    assert coordinator.consume("auto_pick_perks") is True


def test_save_mismatch_ui_already_matches_invalidates_snapshot(monkeypatch):
    coordinator = _coordinator(monkeypatch)
    result = coordinator.acquire(
        {"cards_deck": "Tournament", "auto_pick_perks": True},
        initial_frame=object(),
    )
    assert result.carry is not None

    assert not coordinator.record_ui_verification(
        "cards_deck",
        changed=False,
    )

    assert coordinator.snapshot_invalidated
    assert result.carry.state is CarriedEvidenceState.INVALIDATED
    assert result.carry.values == {}
    assert result.carry.invalidation_reason == "save_ui_contradiction"


def test_observation_only_modules_are_accepted_and_carried(monkeypatch):
    expected = {
        "cannon_primary": "Amplifying Strike",
        "armor_primary": "Orbital Augment",
        "generator_primary": "Project Funding",
        "core_primary": "Dimension Core",
        "cannon_assist": "Being Annihilator",
        "armor_assist": "Anti-Cube Portal",
        "generator_assist": "Singularity Harness",
        "core_assist": "Harmony Conductor",
    }
    observed = {
        **expected,
        "armor_primary": "Anti-Cube Portal",
        "armor_assist": "Space Displacer",
    }
    supported_names = {
        key: [expected[key], observed[key]]
        if expected[key] != observed[key]
        else [expected[key]]
        for key in expected
    }
    base_snapshot = _snapshot()
    snapshot = replace(
        base_snapshot,
        validated_checks=(*base_snapshot.validated_checks, "modules"),
        checks={
            **base_snapshot.checks,
            "modules": SaveCheckEvidence(
                "modules",
                "observed",
                observed,
                ("moduleEquipped", "assistModuleSlots"),
                complete=True,
                authority={
                    "kind": "slot_scoped_module_values",
                    "assignments": observed,
                    "supported_names": supported_names,
                },
            ),
        },
    )
    coordinator = _coordinator(
        monkeypatch,
        decode_fn=lambda _payload, **_kwargs: snapshot,
    )

    result = coordinator.acquire(
        {
            "modules": expected,
            "loadout_policies": {"modules": "observe"},
        },
        initial_frame=object(),
    )

    assert result.ready
    assert result.accepted_checks == ("modules",)
    assert result.decisions["modules"]["disposition"] == "save_observation"
    assert result.decisions["modules"]["matches"] is False
    assert result.carry is not None
    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    assert coordinator.bind_running(
        battle_started=True,
        stable_running=True,
        continuity_verified=True,
    )
    assert coordinator.consume("modules") == observed


def test_same_home_snapshot_publishes_structural_history_baseline(monkeypatch):
    pulls = []
    coordinator = _coordinator(
        monkeypatch,
        pull_fn=lambda **kwargs: pulls.append(kwargs) or b"stable-save",
        decode_fn=lambda _payload, **_kwargs: _snapshot_with_history(
            semantic_status="unavailable"
        ),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert len(pulls) == 1
    assert result.history_scope_id == "activity-private"
    assert result.history_tail["disposition"] == "save_match"
    metadata = result.history_tail["metadata"]
    assert metadata["source"] == "player_save"
    assert metadata["fingerprint"] == "b" * 64
    assert metadata["entry_count"] == metadata["capacity"] == 30
    assert metadata["semantic_status"] == "unavailable"
    assert "activity-private" not in json.dumps(result.as_dict())


def test_malformed_history_projection_does_not_poison_configuration_projector(
    monkeypatch,
):
    monkeypatch.setattr(
        "core.player_save_preflight.history_metadata_from_acquisition",
        lambda _acquisition: (_ for _ in ()).throw(
            ValueError("malformed structural projection")
        ),
    )
    pulls = []
    coordinator = _coordinator(
        monkeypatch,
        pull_fn=lambda **kwargs: pulls.append(kwargs) or b"stable-save",
        decode_fn=lambda _payload, **_kwargs: _snapshot(),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert len(pulls) == 1
    assert result.ready
    assert result.decisions["cards_deck"]["disposition"] == "save_match"
    assert result.history_tail["disposition"] == "ui_required"
    assert result.history_tail["reason"] == (
        "runtime_history_projection_unavailable"
    )


@pytest.mark.parametrize("collector_opt_in", ("0", "1"))
def test_collector_opt_in_is_irrelevant_to_preflight_and_history_authority(
    monkeypatch,
    collector_opt_in,
):
    monkeypatch.setenv("THETOWER_PLAYER_SAVE_AUDIT", collector_opt_in)
    pulls = []
    coordinator = _coordinator(
        monkeypatch,
        pull_fn=lambda **kwargs: pulls.append(kwargs) or b"stable-save",
        decode_fn=lambda _payload, **_kwargs: _snapshot_with_history(),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert len(pulls) == 1
    assert result.decisions["cards_deck"]["disposition"] == "save_match"
    assert result.history_tail["disposition"] == "save_match"


def test_comparison_audit_retains_history_ui_even_with_complete_save_tail(
    monkeypatch,
):
    coordinator = _coordinator(
        monkeypatch,
        decode_fn=lambda _payload, **_kwargs: _snapshot_with_history(),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        mode="comparison_audit",
        initial_frame=object(),
    )

    assert result.history_tail["complete"] is True
    assert result.history_tail["disposition"] == "ui_required"
    assert result.history_tail["reason"] == "comparison_audit_requires_ui"


def test_pull_failure_restores_home_and_authorizes_normal_ui_fallback(monkeypatch):
    foreground = []

    def fail_pull(**_kwargs):
        raise RuntimeError("raw private save text")

    coordinator = _coordinator(
        monkeypatch,
        pull_fn=fail_pull,
        foreground_fn=lambda target: foreground.append(target) or True,
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm", "auto_pick_perks": True},
        initial_frame=object(),
    )

    assert result.ready
    assert result.safe_ui_fallback
    assert result.context is not None
    assert set(result.decisions) == {"cards_deck", "auto_pick_perks"}
    assert all(item["ui_required"] for item in result.decisions.values())
    assert foreground == ["private-device-target"]
    assert "raw private save text" not in json.dumps(result.as_dict())


def test_default_pull_and_lifecycle_transports_suppress_private_errors(
    monkeypatch,
):
    import core.adb_utils as adb_utils
    import core.player_save as player_save
    import core.player_save_preflight as module

    reads = []

    def read_device_file(path, **kwargs):
        reads.append((path, kwargs))
        return b"stable-save"

    shell = Mock(return_value=object())
    monkeypatch.setattr(adb_utils, "read_device_file", read_device_file)
    monkeypatch.setattr(adb_utils, "adb_shell", shell)
    monkeypatch.setattr(player_save.time, "sleep", lambda _seconds: None)
    coordinator = _coordinator(
        monkeypatch,
        pull_fn=pull_player_save_bytes,
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert result.ready
    assert len(reads) == 2
    assert all(kwargs["report_errors"] is False for _path, kwargs in reads)
    assert module._background_default("private-device-target")
    assert module._foreground_default("private-device-target")
    assert shell.call_count == 2
    assert all(
        call.kwargs["report_errors"] is False
        for call in shell.call_args_list
    )


def test_target_generation_change_blocks_all_followup_input(monkeypatch):
    targets = iter(
        [
            AdbTargetSnapshot("private-device-target", 1, True),
            AdbTargetSnapshot("private-device-target", 2, True),
        ]
    )
    coordinator = _coordinator(
        monkeypatch,
        target_snapshot_fn=lambda: next(targets),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert result.status is PlayerSavePreflightStatus.BLOCKED
    assert not result.safe_ui_fallback
    assert result.reason == "restored_target_or_new_battle_boundary_unverified"
    assert "acquisition" not in result.provenance


def test_failed_foreground_restoration_blocks_ui_and_battle_progression(monkeypatch):
    coordinator = _coordinator(
        monkeypatch,
        foreground_fn=lambda _target: False,
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert not result.ready
    assert not result.safe_ui_fallback
    assert result.reason == "foreground_restoration_failed"
    assert "acquisition" not in result.provenance


def test_control_interruption_while_backgrounded_cannot_dispatch_restore(
    monkeypatch,
):
    authority = iter((True, False))
    foreground = Mock(return_value=True)
    coordinator = _coordinator(
        monkeypatch,
        action_guard_fn=lambda: next(authority),
        foreground_fn=foreground,
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert not result.ready
    assert result.reason == "control_authority_interrupted_before_foreground"
    foreground.assert_not_called()


def test_lifecycle_logging_has_one_action_two_inputs_and_one_result(monkeypatch):
    import core.player_save_preflight as module

    coordinator = _coordinator(monkeypatch)
    action = Mock()
    device_input = Mock()
    result_log = Mock()
    monkeypatch.setattr(module, "log_action_intent", action)
    monkeypatch.setattr(module, "log_input", device_input)
    monkeypatch.setattr(module, "log_result", result_log)

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert result.ready
    action.assert_called_once()
    assert device_input.call_count == 2
    result_log.assert_called_once()


def test_per_check_diagnostics_log_mapping_support_and_reason(monkeypatch):
    import core.player_save_preflight as module

    coordinator = _coordinator(monkeypatch)
    emit = Mock()
    monkeypatch.setattr(module, "log", emit)

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert result.ready
    messages = [call.args[0] for call in emit.call_args_list]
    assert any(
        "check=cards_deck mapping=data-9-game-1073 "
        "complete=True supported=True disposition=save_match "
        "reason=exact_version_save_match" in message
        for message in messages
    )
    assert any(
        "check=battle_history_tail mapping=data-9-game-1073 complete=False "
        "supported=False disposition=ui_required "
        "reason=runtime_history_projection_unavailable" in message
        for message in messages
    )


def test_force_ui_skips_save_lifecycle_and_comparison_audit_keeps_ui_authority(
    monkeypatch,
):
    lifecycle_calls = []
    coordinator = _coordinator(
        monkeypatch,
        background_fn=lambda _target: lifecycle_calls.append("background") or True,
    )

    requirements = {
        "cards_deck": "Farm",
        "perk_auto_pick_order": ["perk_wave_requirement"],
        "free_upgrade_locks": ["Shockwave Size"],
        "modules": {"cannon_primary": "Amplifying Strike"},
        "target_priority": ["Fast"],
    }
    forced = coordinator.acquire(
        requirements,
        mode="force_ui",
        initial_frame=object(),
    )
    audited = coordinator.acquire(
        requirements,
        mode="comparison_audit",
        initial_frame=object(),
    )

    assert set(forced.decisions) == set(requirements)
    assert all(
        decision["reason"] == "force_ui_policy"
        and decision["ui_required"]
        for decision in forced.decisions.values()
    )
    assert lifecycle_calls == ["background"]
    assert all(
        decision["disposition"] == "ui_required"
        for decision in audited.decisions.values()
    )
    assert audited.carry is None


def test_runtime_policy_metadata_is_not_invented_as_a_ui_check(monkeypatch):
    coordinator = _coordinator(monkeypatch)

    result = coordinator.acquire(
        {
            "cards_deck": "Farm",
            "loadout_policies": {
                "modules": "preserve",
                "target_priority": "preserve",
            },
            "profile_skips": ["perk_bans"],
        },
        mode="force_ui",
        initial_frame=object(),
    )

    assert set(result.decisions) == {"cards_deck"}


def test_force_ui_does_not_require_or_probe_private_target_context(monkeypatch):
    context = Mock(side_effect=AssertionError("force-ui must not acquire context"))
    coordinator = _coordinator(monkeypatch, context_fn=context)

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        mode="force_ui",
        initial_frame=object(),
    )

    assert result.ready
    assert result.decisions["cards_deck"]["reason"] == "force_ui_policy"
    context.assert_not_called()


def test_missing_private_context_fails_closed_without_exception_text(monkeypatch):
    coordinator = _coordinator(
        monkeypatch,
        context_fn=Mock(side_effect=RuntimeError("private target detail")),
    )

    result = coordinator.acquire(
        {"cards_deck": "Farm"},
        initial_frame=object(),
    )

    assert not result.ready
    assert result.reason == "preflight_context_unavailable"
    assert "private target detail" not in json.dumps(result.as_dict())


def test_carry_is_single_use_and_rejects_context_change(monkeypatch):
    current = [_context()]
    coordinator = _coordinator(monkeypatch, context_fn=lambda: current[0])
    result = coordinator.acquire(
        {"cards_deck": "Farm", "auto_pick_perks": True},
        initial_frame=object(),
    )
    carry = result.carry
    assert carry is not None

    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    assert coordinator.bind_running(
        battle_started=True,
        stable_running=True,
        continuity_verified=True,
    )
    assert coordinator.consume("auto_pick_perks") is True
    assert coordinator.consume("auto_pick_perks") is None

    current[0] = _context(strategy="farm_t18")
    assert coordinator.consume("cards_deck") is None
    assert carry.state is CarriedEvidenceState.INVALIDATED
    assert carry.invalidation_reason == "carried_evidence_context_changed"


@pytest.mark.parametrize(
    ("scenario", "launch_kwargs", "bind_kwargs"),
    [
        (
            "operator_resume_or_attach",
            {
                "control": HomeBattleControl.RESUME_BATTLE,
                "action_authorized": True,
                "dispatched": True,
            },
            None,
        ),
        (
            "dispatched_without_action_authority",
            {
                "control": HomeBattleControl.NEW_BATTLE,
                "action_authorized": False,
                "dispatched": True,
            },
            None,
        ),
        (
            "retry_without_home_preflight_launch",
            None,
            {
                "battle_started": True,
                "stable_running": True,
                "continuity_verified": True,
            },
        ),
    ],
)
def test_carry_rejects_non_owned_launch_transitions(
    monkeypatch,
    scenario,
    launch_kwargs,
    bind_kwargs,
):
    coordinator = _coordinator(monkeypatch)
    carry = coordinator.acquire(
        {"auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None, scenario

    if launch_kwargs is not None:
        launch_accepted = coordinator.mark_runtime_launch(**launch_kwargs)
        if bind_kwargs is None:
            assert not launch_accepted, scenario
    if bind_kwargs is not None:
        assert not coordinator.bind_running(**bind_kwargs), scenario

    assert carry.state is CarriedEvidenceState.INVALIDATED


def test_no_dispatch_remains_pending_for_a_later_verified_tap(monkeypatch):
    coordinator = _coordinator(monkeypatch)
    carry = coordinator.acquire(
        {"auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None

    assert not coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=False,
    )
    assert carry.state is CarriedEvidenceState.PENDING_LAUNCH
    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )


def test_pause_suspends_carry_without_quarantining_snapshot(monkeypatch):
    coordinator = _coordinator(monkeypatch)
    carry = coordinator.acquire(
        {"auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None

    coordinator.suspend_carry("pause_requires_fresh_running_evidence")

    assert carry.state is CarriedEvidenceState.SUSPENDED
    assert carry.values == {"auto_pick_perks": True}
    assert not coordinator.snapshot_invalidated
    assert coordinator.consume("auto_pick_perks") is None


def test_unstable_first_running_frame_defers_until_stable(monkeypatch):
    coordinator = _coordinator(monkeypatch)
    carry = coordinator.acquire(
        {"auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None
    assert coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )

    assert not coordinator.bind_running(
        battle_started=True,
        stable_running=False,
        continuity_verified=True,
    )
    assert carry.state is CarriedEvidenceState.LAUNCH_DISPATCHED
    assert coordinator.bind_running(
        battle_started=False,
        stable_running=True,
        continuity_verified=True,
    )
    assert carry.state is CarriedEvidenceState.BOUND_RUNNING


@pytest.mark.parametrize(
    "changed_context",
    [
        replace(_context(), runtime_session_id="restarted-runtime"),
        replace(_context(), preflight_session_id="later-preflight"),
        replace(_context(), activity_scope_id="unrelated-battle"),
        replace(_context(), strategy_name="farm_t18"),
        replace(_context(), configuration_fingerprint="e" * 64),
        replace(_context(), target="replacement-target"),
        replace(_context(), target_generation=2),
    ],
)
def test_carry_rejects_every_context_continuity_change(
    monkeypatch,
    changed_context,
):
    current = [_context()]
    coordinator = _coordinator(monkeypatch, context_fn=lambda: current[0])
    carry = coordinator.acquire(
        {"auto_pick_perks": True},
        initial_frame=object(),
    ).carry
    assert carry is not None
    current[0] = changed_context

    assert not coordinator.mark_runtime_launch(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    assert carry.state is CarriedEvidenceState.INVALIDATED
