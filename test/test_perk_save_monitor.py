from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from core.perk_save_monitor import (
    PerkSaveMonitor,
    PerkSaveMonitorContext,
    merge_terminal_perk_evidence,
    merge_terminal_perk_tail,
)
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveBoundaryKind,
    PlayerSaveNaturalBoundary,
    PlayerSaveTargetBinding,
)
from core.runtime_save import (
    ActiveRoundIdentity,
    BattleHistoryTail,
    NormalizedRuntimeSave,
    RuntimePerkPick,
    RuntimePerkSnapshot,
)


UTC = timezone.utc
START = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _context(*, generation: int = 4, activity: str = "scope-1"):
    return PerkSaveMonitorContext(
        runtime_session_id="runtime-1",
        activity_scope_id=activity,
        target_binding=PlayerSaveTargetBinding(
            "localhost:5555",
            generation,
        ),
    )


def _identity(*, seed: int = 12345) -> ActiveRoundIdentity:
    return ActiveRoundIdentity(
        game_version=1073,
        current_tier=22,
        rounds_started_this_tier=9,
        round_seed=seed,
        fingerprint=_sha(f"round:{seed}"),
    )


def _history_tail() -> BattleHistoryTail:
    return BattleHistoryTail(
        structural_status="empty",
        structural_reason="battle_history_empty",
        entry_count=0,
        capacity=30,
        identity=None,
        completed_entry_status="not_applicable",
        completed_entry_reason="battle_history_empty",
        entry=None,
    )


def _runtime(
    revision: int,
    *,
    saved_wave: int,
    picks: tuple[RuntimePerkPick, ...] = (),
    identity: ActiveRoundIdentity | None = None,
    captured_offset: int | None = None,
    perks_status: str = "observed",
    perks_reason: str = "available",
    perks: RuntimePerkSnapshot | None = None,
) -> NormalizedRuntimeSave:
    levels_by_id: dict[int, tuple[str, int]] = {}
    for pick in picks:
        levels_by_id[pick.perk_id] = (pick.perk_key, pick.level_after)
    projection = perks
    if projection is None and perks_status == "observed":
        projection = RuntimePerkSnapshot(
            state="active_round",
            picked_count=len(picks),
            levels=tuple(
                (perk_id, key, level)
                for perk_id, (key, level) in sorted(levels_by_id.items())
            ),
            picks=picks,
            fingerprint=_sha(f"perks:{revision}:{picks!r}"),
        )
    return NormalizedRuntimeSave(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": (
                START + timedelta(seconds=captured_offset or revision)
            ).isoformat(),
            "source_sha256": _sha(f"save:{revision}"),
        },
        save_revision=revision,
        round_active=True,
        current_wave=saved_wave,
        active_round_identity=identity or _identity(),
        perks_status=perks_status,
        perks_reason=perks_reason,
        perks=projection,
        battle_history_tail=_history_tail(),
    )


def _terminal_runtime(revision: int, *, captured_offset: int) -> NormalizedRuntimeSave:
    return NormalizedRuntimeSave(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": (START + timedelta(seconds=captured_offset)).isoformat(),
            "source_sha256": _sha(f"terminal:{revision}"),
        },
        save_revision=revision,
        round_active=False,
        current_wave=0,
        active_round_identity=None,
        perks_status="observed",
        perks_reason="available",
        perks=RuntimePerkSnapshot(
            state="cleared",
            picked_count=0,
            levels=(),
            picks=(),
            fingerprint=_sha("cleared"),
        ),
        battle_history_tail=_history_tail(),
    )


def _pick(
    sequence: int,
    wave: int,
    perk_id: int,
    perk_key: str,
    level_after: int = 1,
) -> RuntimePerkPick:
    return RuntimePerkPick(sequence, wave, perk_id, perk_key, level_after)


def _observe(
    monitor: PerkSaveMonitor,
    runtime: NormalizedRuntimeSave,
    *,
    context: PerkSaveMonitorContext | None = None,
    acquisition_type: PlayerSaveAcquisitionType | None = None,
    boundary_scope: str | None = None,
    boundary_lead_ms: int = 200,
) -> str:
    bound_context = context or _context()
    captured = datetime.fromisoformat(str(runtime.capture["captured_at"]))
    acquisition_type = acquisition_type or (
        PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
        if runtime.round_active
        else PlayerSaveAcquisitionType.NATURAL_BOUNDARY
    )
    boundary = (
        PlayerSaveNaturalBoundary(
            PlayerSaveBoundaryKind.GAME_OVER,
            captured - timedelta(milliseconds=boundary_lead_ms),
            bound_context.runtime_session_id,
            boundary_scope or bound_context.activity_scope_id,
        )
        if acquisition_type is PlayerSaveAcquisitionType.NATURAL_BOUNDARY
        else None
    )
    snapshot = SimpleNamespace(
        runtime_save=runtime,
        game_version=1073,
        mapping_id=runtime.mapping_id,
        shape_valid=True,
        mapping_supported=True,
    )
    acquisition = PlayerSaveAcquisitionBundle(
        acquisition_type=acquisition_type,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=bound_context.target_binding,
        acquisition_started_at=captured - timedelta(milliseconds=150),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=25),
        transport_stable=True,
        snapshot=snapshot,
        boundary=boundary,
    )
    return monitor.observe_bundle(acquisition, context=bound_context)


def _exhaustion(*, wave: int = 200, offset: int = 20) -> dict:
    observed = START + timedelta(seconds=offset)
    return {
        "source": "stable_top_bar_view_perks",
        "event_id": _sha(f"view-perks:{wave}:{offset}"),
        "activity_scope_id": "scope-1",
        "observed_wave": wave,
        "observed_at": observed.isoformat(),
        "stable_observation_count": 2,
        "ocr_confidence": 94.0,
        "capture_provenance": {
            "source": "main_loop_frame",
            "region": "perk_progress_text",
            "source_fingerprint": _sha(f"frame:{wave}:{offset}"),
        },
    }


def _qualified_monitoring() -> dict:
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    final = first + (_pick(2, 200, 2, "damage"),)
    assert _observe(
        monitor,
        _runtime(10, saved_wave=180, picks=first, captured_offset=10),
    ) == "initial_complete_prefix"
    assert monitor.observe_exhaustion(_exhaustion(), context=_context())
    assert _observe(
        monitor,
        _runtime(11, saved_wave=220, picks=final, captured_offset=30),
    ) == "strict_prefix_extension"
    assert _observe(
        monitor,
        _terminal_runtime(12, captured_offset=40),
    ) == "terminal_cleared_prefix_retained"
    return monitor.terminal_evidence(context=_context(), terminal_state="GAME_OVER")


def test_accepts_first_unchanged_newer_and_strict_prefix_extension():
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    extended = first + (_pick(2, 150, 2, "damage"),)

    assert _observe(monitor, _runtime(1, saved_wave=120, picks=first)) == (
        "initial_complete_prefix"
    )
    assert _observe(monitor, _runtime(2, saved_wave=130, picks=first)) == (
        "unchanged_complete_prefix_observed_later"
    )
    assert _observe(monitor, _runtime(3, saved_wave=160, picks=extended)) == (
        "strict_prefix_extension"
    )

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["checkpoint"]["acceptance"] == "strict_prefix_extension"
    assert [pick["perk_key"] for pick in evidence["checkpoint"]["picks"]] == [
        "max_health",
        "damage",
    ]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (_runtime(1, saved_wave=130, picks=(_pick(1, 100, 1, "max_health"),)),
         "ignored_lagging_same_prefix"),
        (_runtime(3, saved_wave=140, picks=(_pick(1, 100, 1, "damage"),)),
         "rejected_non_prefix"),
        (_runtime(3, saved_wave=140, picks=(), identity=_identity(seed=999)),
         "rejected_identity"),
    ],
)
def test_rejects_stale_non_prefix_and_wrong_identity(candidate, expected):
    monitor = PerkSaveMonitor()
    assert _observe(
        monitor,
        _runtime(
            2,
            saved_wave=120,
            picks=(_pick(1, 100, 1, "max_health"),),
        ),
    ) == "initial_complete_prefix"
    assert _observe(monitor, candidate) == expected
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["status"] == "fallback_required"
    assert evidence["checkpoint"]["save_revision"] == 2


def test_rejects_target_generation_change_without_rebinding_or_publishing():
    monitor = PerkSaveMonitor()
    runtime = _runtime(1, saved_wave=100)
    assert _observe(monitor, runtime) == "initial_complete_prefix"

    assert _observe(monitor, replace(runtime, save_revision=2), context=_context(generation=5)) == (
        "rejected_binding"
    )
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["status"] == "fallback_required"
    assert evidence["checkpoint"]["save_revision"] == 1


def test_prefix_regression_keeps_final_completeness_failed_closed_for_round():
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    extended = first + (_pick(2, 150, 2, "damage"),)
    assert _observe(
        monitor,
        _runtime(1, saved_wave=170, picks=extended),
    ) == "initial_complete_prefix"
    assert _observe(
        monitor,
        _runtime(2, saved_wave=180, picks=first),
    ) == "rejected_prefix_regression"
    assert _observe(
        monitor,
        _runtime(3, saved_wave=220, picks=extended),
    ) == "unchanged_complete_prefix_observed_later"

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["round_conflict_reason"] == "complete_prefix_regressed"


def test_prefix_extension_captured_before_checkpoint_is_a_sticky_conflict():
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    extended = first + (_pick(2, 150, 2, "damage"),)
    assert _observe(
        monitor,
        _runtime(10, saved_wave=120, picks=first, captured_offset=20),
    ) == "initial_complete_prefix"

    assert _observe(
        monitor,
        _runtime(11, saved_wave=180, picks=extended, captured_offset=10),
    ) == "rejected_prefix_conflict"
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["checkpoint"]["picked_count"] == 1
    assert evidence["round_conflict_reason"] == (
        "prefix_extension_predates_complete_checkpoint"
    )
    assert evidence["status"] == "fallback_required"


def test_rejects_malformed_and_unknown_perk_components_atomically():
    malformed = RuntimePerkSnapshot(
        state="active_round",
        picked_count=1,
        levels=((1, "max_health", 2),),
        picks=(_pick(1, 100, 1, "max_health", 2),),
        fingerprint=_sha("malformed"),
    )
    monitor = PerkSaveMonitor()
    assert _observe(
        monitor,
        _runtime(1, saved_wave=120, perks=malformed),
    ) == "rejected_perks"
    assert monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )["checkpoint"] is None

    monitor = PerkSaveMonitor()
    assert _observe(
        monitor,
        _runtime(
            1,
            saved_wave=120,
            perks_status="unavailable",
            perks_reason="unmapped_perk_id:11",
        ),
    ) == "rejected_perks"
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["checkpoint"] is None
    assert evidence["active_failure_reason"] == "unmapped_perk_id_11"


def test_terminal_cleared_projection_retains_newest_complete_active_prefix():
    monitor = PerkSaveMonitor()
    picks = (_pick(1, 100, 1, "max_health"),)
    assert _observe(monitor, _runtime(5, saved_wave=150, picks=picks)) == (
        "initial_complete_prefix"
    )
    assert _observe(monitor, _terminal_runtime(6, captured_offset=30)) == (
        "terminal_cleared_prefix_retained"
    )

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["checkpoint"]["save_revision"] == 5
    assert evidence["checkpoint"]["picked_count"] == 1
    assert evidence["terminal_window"]["save_revision"] == 6


def test_terminal_clear_uses_natural_boundary_not_revision_as_freshness():
    monitor = PerkSaveMonitor()
    active = _runtime(5, saved_wave=150)
    assert _observe(monitor, active) == "initial_complete_prefix"

    assert _observe(monitor, _terminal_runtime(5, captured_offset=30)) == (
        "terminal_cleared_prefix_retained"
    )
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["terminal_window"]["save_revision"] == 5
    assert evidence["active_failure_reason"] is None


def test_passive_clear_never_closes_the_terminal_checkpoint_window():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(5, saved_wave=150)) == (
        "initial_complete_prefix"
    )

    assert _observe(
        monitor,
        _terminal_runtime(6, captured_offset=30),
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
    ) == "rejected_unbound_terminal_clear"
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["terminal_window"] is None
    assert evidence["ui_fallback"]["required"] is True


def test_natural_clear_with_wrong_activity_scope_fails_closed():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(5, saved_wave=150)) == (
        "initial_complete_prefix"
    )

    assert _observe(
        monitor,
        _terminal_runtime(6, captured_offset=30),
        boundary_scope="another-scope",
    ) == "rejected_unbound_terminal_clear"
    assert monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )["terminal_window"] is None


def test_monitor_evidence_redacts_private_runtime_scope_and_target():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(1, saved_wave=150)) == (
        "initial_complete_prefix"
    )

    rendered = json.dumps(
        monitor.terminal_evidence(
            context=_context(),
            terminal_state="GAME_OVER",
        )
    )
    assert "localhost:5555" not in rendered
    assert "scope-1" not in rendered
    assert "runtime-1" not in rendered


def test_failed_forced_attachment_never_publishes_or_erases_prior_prefix():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(1, saved_wave=150)) == (
        "initial_complete_prefix"
    )
    context = _context()
    failed = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
        status=PlayerSaveAcquisitionStatus.UNAVAILABLE,
        reason="source_restoration_ambiguous",
        binding=context.target_binding,
        acquisition_started_at=START + timedelta(seconds=2),
        captured_at=None,
        acquisition_completed_at=START + timedelta(seconds=3),
        transport_stable=False,
    )

    assert monitor.observe_bundle(failed, context=context) == (
        "rejected_acquisition"
    )
    evidence = monitor.terminal_evidence(
        context=context,
        terminal_state="GAME_OVER",
    )
    assert evidence["checkpoint"]["save_revision"] == 1
    assert evidence["status"] == "fallback_required"
    assert evidence["reason"] == "source_restoration_ambiguous"


def test_bound_checkpoint_accessor_retains_positive_prefix_after_later_failure():
    monitor = PerkSaveMonitor()
    context = _context()
    assert _observe(
        monitor,
        _runtime(
            1,
            saved_wave=150,
            picks=(_pick(1, 100, 1, "max_health"),),
        ),
        context=context,
    ) == "initial_complete_prefix"
    failed = PlayerSaveAcquisitionBundle(
        acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
        status=PlayerSaveAcquisitionStatus.UNAVAILABLE,
        reason="passive_source_temporarily_unavailable",
        binding=context.target_binding,
        acquisition_started_at=START + timedelta(seconds=20),
        captured_at=None,
        acquisition_completed_at=START + timedelta(seconds=21),
        transport_stable=False,
    )
    assert monitor.observe_bundle(failed, context=context) == "rejected_acquisition"

    checkpoint = monitor.bound_checkpoint_evidence(context)
    assert checkpoint is not None
    assert checkpoint["picked_count"] == 1
    assert checkpoint["picks"][0]["perk_key"] == "max_health"
    assert monitor.bound_checkpoint_evidence(_context(generation=9)) is None


def test_post_exhaustion_checkpoint_and_terminal_window_close_exact_inventory():
    evidence = _qualified_monitoring()

    assert evidence["status"] == "complete_final_prefix"
    assert evidence["ui_fallback"]["required"] is False
    inventory = evidence["final_inventory"]
    assert inventory["status"] == "complete_exact_saved_inventory"
    assert [pick["sequence"] for pick in inventory["exact_saved_picks"]] == [1, 2]
    assert [pick["saved_wave"] for pick in inventory["exact_saved_picks"]] == [
        100,
        200,
    ]
    assert evidence["exhaustion"]["active_round_identity"] == (
        evidence["active_round_identity"]
    )


def test_conflicting_exhaustion_events_fail_closed_for_the_round():
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    assert _observe(
        monitor,
        _runtime(10, saved_wave=180, picks=first, captured_offset=10),
    ) == "initial_complete_prefix"
    assert monitor.observe_exhaustion(_exhaustion(), context=_context())
    assert monitor.observe_exhaustion(
        _exhaustion(wave=201, offset=21), context=_context()
    ) is False
    assert _observe(
        monitor,
        _runtime(11, saved_wave=220, picks=first, captured_offset=30),
    ) == "unchanged_complete_prefix_observed_later"
    assert _observe(monitor, _terminal_runtime(12, captured_offset=40)) == (
        "terminal_cleared_prefix_retained"
    )

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["status"] == "fallback_required"
    assert evidence["round_conflict_reason"] == "conflicting_exhaustion_evidence"


def test_boundary_issued_at_acquisition_start_cannot_close_the_window():
    monitor = PerkSaveMonitor()
    first = (_pick(1, 100, 1, "max_health"),)
    assert _observe(
        monitor,
        _runtime(10, saved_wave=180, picks=first, captured_offset=10),
    ) == "initial_complete_prefix"

    assert _observe(
        monitor,
        _terminal_runtime(11, captured_offset=30),
        boundary_lead_ms=150,
    ) == "rejected_unbound_terminal_clear"


def test_view_perks_with_only_pre_exhaustion_checkpoint_requires_terminal_ui():
    monitor = PerkSaveMonitor()
    assert _observe(
        monitor,
        _runtime(10, saved_wave=180, captured_offset=10),
    ) == "initial_complete_prefix"
    assert monitor.observe_exhaustion(_exhaustion(), context=_context())
    assert _observe(monitor, _terminal_runtime(11, captured_offset=30)) == (
        "terminal_cleared_prefix_retained"
    )

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["status"] == "fallback_required"
    assert evidence["reason"] == "checkpoint_predates_exhaustion"
    assert evidence["ui_fallback"]["required"] is True


def test_persisted_exhaustion_with_wrong_active_identity_fails_closed():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(10, saved_wave=180)) == (
        "initial_complete_prefix"
    )
    exhaustion = _exhaustion()
    exhaustion["binding_status"] = "active_round_identity_bound"
    exhaustion["active_round_identity"] = _identity(seed=999).as_dict()

    assert monitor.observe_exhaustion(exhaustion, context=_context()) is False
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["exhaustion"] is None
    assert evidence["active_failure_reason"] == "exhaustion_identity_mismatch"


def test_post_observation_capture_with_lagging_saved_wave_is_not_final():
    monitor = PerkSaveMonitor()
    assert _observe(
        monitor,
        _runtime(10, saved_wave=180, captured_offset=10),
    ) == "initial_complete_prefix"
    assert monitor.observe_exhaustion(_exhaustion(wave=200), context=_context())
    assert _observe(
        monitor,
        _runtime(11, saved_wave=190, captured_offset=30),
    ) == "unchanged_complete_prefix_observed_later"
    assert _observe(monitor, _terminal_runtime(12, captured_offset=40)) == (
        "terminal_cleared_prefix_retained"
    )

    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["status"] == "fallback_required"
    assert evidence["reason"] == "checkpoint_wave_predates_exhaustion"


def test_missing_exhaustion_evidence_requires_terminal_ui():
    monitor = PerkSaveMonitor()
    assert _observe(monitor, _runtime(1, saved_wave=200)) == (
        "initial_complete_prefix"
    )
    assert _observe(monitor, _terminal_runtime(2, captured_offset=30)) == (
        "terminal_cleared_prefix_retained"
    )
    evidence = monitor.terminal_evidence(
        context=_context(), terminal_state="GAME_OVER"
    )
    assert evidence["reason"] == "exhaustion_not_authoritatively_observed"


def test_terminal_tail_merge_keeps_ambiguous_additions_in_a_bounded_interval():
    monitoring = _qualified_monitoring()
    terminal_ui = {
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "display_text": "x1.44 Damage",
                "latest_selection_rank": 1,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 2,
            },
            {
                "display_text": "x1.25 Max Health",
                "latest_selection_rank": 2,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 1,
            },
            {
                "display_text": "Perk Wave Requirement -75%",
                "latest_selection_rank": 3,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 1,
            },
        ],
        "quality": {"valid": True, "source_complete": True, "warnings": []},
    }

    inventory, merge = merge_terminal_perk_evidence(
        monitoring,
        terminal_ui,
        top_bar_timeline={"passive_top_bar": {"selection_boundaries": []}},
        game_over_wave=400,
    )

    assert inventory is not None
    assert merge["status"] == "complete"
    assert merge["tail_correspondence"] == "interval_or_unknown"
    assert len(merge["tail_aggregates"]) == 2
    assert all(item["sequence"] is None for item in merge["tail_aggregates"])
    assert all(item["wave"] is None for item in merge["tail_aggregates"])
    assert all(
        item["interval"]
        == {
            "after_saved_wave_exclusive": 220,
            "before_game_over_wave_inclusive": 400,
        }
        for item in merge["tail_aggregates"]
    )


def test_terminal_tail_uses_exact_schedule_only_for_unique_correspondence():
    monitoring = _qualified_monitoring()
    terminal_ui = {
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "display_text": "Perk Wave Requirement -25%",
                "latest_selection_rank": 1,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 1,
            },
            {
                "display_text": "x1.44 Damage",
                "latest_selection_rank": 2,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 1,
            },
            {
                "display_text": "x1.25 Max Health",
                "latest_selection_rank": 3,
                "instance_model": "leveled",
                "confidence": 95.0,
                "final_level": 1,
            },
        ],
        "quality": {"valid": True, "source_complete": True, "warnings": []},
    }
    timeline = {
        "passive_top_bar": {
            "selection_boundaries": [
                {
                    "scheduled_wave": 300,
                    "boundary_coverage": "complete",
                }
            ]
        }
    }

    inventory, merge = merge_terminal_perk_evidence(
        monitoring,
        terminal_ui,
        top_bar_timeline=timeline,
        game_over_wave=400,
    )

    assert inventory is not None
    assert merge["tail_correspondence"] == "unique"
    assert merge["tail_aggregates"] == [
        {
            "perk_key": "perk_wave_requirement",
            "kind": "aggregate_addition",
            "level_before": 0,
            "level_after": 1,
            "net_level_change": 1,
            "latest_selection_rank": 1,
            "sequence": 3,
            "wave": 300,
            "order_status": "exact_unique_correspondence",
            "wave_status": "exact_passive_schedule_correspondence",
        }
    ]


def test_terminal_top_prefix_fills_only_tail_before_saved_recency_marker():
    monitoring = _qualified_monitoring()
    terminal_top = {
        "source_method": "terminal_perks_top_prefix_ocr",
        "capture_scope": "newest_prefix_until_saved_recency",
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "display_text": "Perk Wave Requirement -25%",
                "latest_selection_rank": 1,
                "instance_model": "leveled",
                "confidence": 95.0,
            },
            {
                "display_text": "x1.44 Damage",
                "latest_selection_rank": 2,
                "instance_model": "leveled",
                "confidence": 95.0,
            },
            {
                "display_text": "x1.25 Max Health",
                "latest_selection_rank": 3,
                "instance_model": "leveled",
                "confidence": 95.0,
            },
        ],
        "quality": {
            "valid": True,
            "scope_complete": True,
            "inventory_complete": False,
        },
    }
    timeline = {
        "passive_top_bar": {
            "selection_boundaries": [
                {"scheduled_wave": 300, "boundary_coverage": "complete"}
            ]
        }
    }

    inventory, merge = merge_terminal_perk_tail(
        monitoring,
        terminal_top,
        top_bar_timeline=timeline,
        game_over_wave=400,
    )

    assert inventory is not None
    assert inventory["source_method"] == (
        "player_save_checkpoint_plus_terminal_top_prefix"
    )
    assert inventory["terminal_tail"]["capture_scope"] == (
        "newest_prefix_until_saved_recency"
    )
    assert merge["overlap_marker"]["perk_key"] == "damage"
    assert merge["tail_correspondence"] == "unique"
    assert merge["tail_aggregates"] == [
        {
            "perk_key": "perk_wave_requirement",
            "kind": "aggregate_addition",
            "level_before": 0,
            "level_after": 1,
            "net_level_change": 1,
            "minimum_level_change": 1,
            "latest_selection_rank": 1,
            "display_text": "Perk Wave Requirement -25%",
            "sequence": 3,
            "wave": 300,
            "order_status": "exact_unique_correspondence",
            "wave_status": "exact_passive_schedule_correspondence",
        }
    ]


def test_terminal_top_prefix_preserves_uncertainty_when_marker_is_not_visible():
    monitoring = _qualified_monitoring()
    monitoring["active_failure_reason"] = "later_passive_read_failed"
    terminal_top = {
        "capture_scope": "newest_visible_prefix",
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "display_text": "Orbs +1",
                "latest_selection_rank": 1,
                "instance_model": "single_instance",
                "confidence": 96.0,
            },
            {
                "display_text": "Perk Wave Requirement -25%",
                "latest_selection_rank": 2,
                "instance_model": "leveled",
                "confidence": 95.0,
            },
        ],
        "quality": {"valid": True, "scope_complete": True},
    }

    inventory, merge = merge_terminal_perk_tail(
        monitoring,
        terminal_top,
        game_over_wave=400,
    )

    assert inventory is not None
    assert merge["overlap_marker"] is None
    assert merge["tail_correspondence"] == "interval_or_unresolved"
    assert {item["perk_key"] for item in merge["tail_aggregates"]} == {
        "orbs",
        "perk_wave_requirement",
    }
    assert all(item["sequence"] is None for item in merge["tail_aggregates"])
    assert any(
        "later save observation failed" in warning
        for warning in inventory["quality"]["warnings"]
    )


@pytest.mark.parametrize(
    "terminal_ui",
    [
        {"selected": [], "quality": {"valid": False, "source_complete": False}},
        {
            "selected": [
                {
                    "display_text": "x1.25 Max Health",
                    "latest_selection_rank": 1,
                    "instance_model": "leveled",
                    "confidence": 95.0,
                    "final_level": 1,
                }
            ],
            "quality": {"valid": True, "source_complete": True},
        },
    ],
)
def test_terminal_tail_incomplete_or_conflicting_evidence_fails_closed(terminal_ui):
    inventory, merge = merge_terminal_perk_evidence(
        _qualified_monitoring(), terminal_ui, game_over_wave=400
    )

    assert inventory is None
    assert merge["status"] == "conflict"
    assert merge["reason"] in {
        "terminal_ui_incomplete",
        "terminal_ui_contradicts_saved_prefix",
    }
