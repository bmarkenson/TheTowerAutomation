from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest

from core.active_run_metric_monitor import ActiveRunMetricMonitor
from core.perk_save_monitor import PerkSaveMonitorContext
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
    ActiveRunTalliesSnapshot,
    BattleHistoryTail,
    BattleHistoryTailIdentity,
    NormalizedRuntimeSave,
    RuntimeTallyClaimDefinition,
    RuntimeTallyComponent,
    RuntimeTallyMetric,
)
from core.terminal_save_report import terminal_save_report_from_acquisition


UTC = timezone.utc
START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
CAPABILITY_ID = "thetower.player_save.active_run_tallies.v1"
SEMANTIC_FINGERPRINT = hashlib.sha256(b"tally-semantics").hexdigest()
BINDING_FINGERPRINT = hashlib.sha256(b"tally-bindings").hexdigest()
SEMANTIC_MAPPING_ID = "data-10-game-1102-semantic-via-1101"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _context(
    *,
    activity: str = "scope-1",
    generation: int = 4,
    identity: str = "a" * 64,
):
    return PerkSaveMonitorContext(
        runtime_session_id="runtime-1",
        activity_scope_id=activity,
        active_round_identity_fingerprint=identity,
        target_binding=PlayerSaveTargetBinding("localhost:5555", generation),
    )


def _metric(
    value: int | float,
    *,
    unit: str,
    source: str,
    terminal_source: str | None = None,
) -> RuntimeTallyMetric:
    return RuntimeTallyMetric(
        value_type="number",
        value=value,
        value_decimal=str(value),
        unit=unit,
        source_fields=(source,),
        derivation="direct",
        semantic_id=f"{CAPABILITY_ID}.test.{source}",
        semantic_fingerprint=_sha(f"semantic:{source}:{unit}"),
        terminal_source=terminal_source,
    )


def _derived(value: int | float, *, unit: str) -> RuntimeTallyMetric:
    return RuntimeTallyMetric(
        value_type="decimal",
        value=value,
        value_decimal=str(value),
        unit=unit,
        source_fields=("syntheticNumerator", "syntheticDenominator"),
        derivation="ratio",
        semantic_id=f"{CAPABILITY_ID}.test.derived.{unit}",
        semantic_fingerprint=_sha(f"derived:{unit}"),
    )


def _claim_definition(metric: RuntimeTallyMetric) -> RuntimeTallyClaimDefinition:
    return RuntimeTallyClaimDefinition(
        unit=metric.unit,
        source_fields=metric.source_fields,
        derivation=metric.derivation,
        semantic_id=metric.semantic_id,
        semantic_fingerprint=metric.semantic_fingerprint,
        terminal_source=metric.terminal_source,
    )


def _tallies(
    *,
    real: float,
    game: float,
    coins: float,
    cells: float,
    cash: float,
    progress: int,
    coin_source: float,
    resource: float,
) -> ActiveRunTalliesSnapshot:
    average_cph = coins * 3600 / real if real else 0
    snapshot = ActiveRunTalliesSnapshot(
        status="observed",
        reason="",
        state="active_round",
        capability_id=CAPABILITY_ID,
        semantic_fingerprint=SEMANTIC_FINGERPRINT,
        binding_fingerprint=BINDING_FINGERPRINT,
        forward_policy="additive_dependencies",
        audit_id="V1101-RUNTIME-017",
        evidence_level="cross_channel",
        components=(
            RuntimeTallyComponent(
                name="economy",
                status="observed",
                reason="",
                metrics=(
                    (
                        "cash_earned",
                        _metric(
                            cash,
                            unit="cash",
                            source="cashEarnedThisRound",
                            terminal_source="cashEarned",
                        ),
                    ),
                    (
                        "cells_earned",
                        _metric(
                            cells,
                            unit="cells",
                            source="cellsEarnedThisRound",
                            terminal_source="cellsEarned",
                        ),
                    ),
                    (
                        "coins_earned",
                        _metric(
                            coins,
                            unit="coins",
                            source="coinsEarnedThisRound",
                            terminal_source="coinsEarned",
                        ),
                    ),
                    (
                        "game_time_seconds",
                        _metric(
                            game,
                            unit="seconds",
                            source="gameplayTimeThisRound",
                            terminal_source="gameTime",
                        ),
                    ),
                    (
                        "real_time_seconds",
                        _metric(
                            real,
                            unit="seconds",
                            source="realTimeThisRound",
                            terminal_source="realTime",
                        ),
                    ),
                ),
                derived=(
                    (
                        "average_coins_per_hour",
                        _derived(average_cph, unit="coins_per_hour"),
                    ),
                ),
            ),
            RuntimeTallyComponent(
                name="progress",
                status="observed",
                reason="",
                metrics=(
                    (
                        "waves_skipped",
                        _metric(
                            progress,
                            unit="waves",
                            source="wavesSkippedThisRound",
                            terminal_source="wavesSkipped",
                        ),
                    ),
                ),
            ),
            RuntimeTallyComponent(
                name="coin_sources",
                status="observed",
                reason="",
                metrics=(
                    (
                        "coins_from_black_hole",
                        _metric(
                            coin_source,
                            unit="coins",
                            source="blackHoleCoinsThisRound",
                            terminal_source="coinsFromBlackHole",
                        ),
                    ),
                ),
            ),
            RuntimeTallyComponent(
                name="resources",
                status="observed",
                reason="",
                metrics=(
                    (
                        "reroll_shards_earned",
                        _metric(
                            resource,
                            unit="shards",
                            source="rerollCurrencyEarnedThisRound",
                            terminal_source="rerollShardsEarned",
                        ),
                    ),
                ),
            ),
        ),
    )
    return replace(
        snapshot,
        components=tuple(
            replace(
                component,
                claim_definitions=tuple(
                    (name, _claim_definition(metric))
                    for name, metric in component.metrics
                ),
            )
            for component in snapshot.components
        ),
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


def _semantic_terminal_tail(
    label: str,
    *,
    count: int,
    wave: int,
    is_tournament: bool = False,
    terminal_metric_claims: dict | None = None,
) -> BattleHistoryTail:
    identity = BattleHistoryTailIdentity(
        mapping_id=SEMANTIC_MAPPING_ID,
        battle_date={
            "kind_id": 2,
            "kind": "local",
            "clock_basis": "local_wall_clock_without_offset",
            "clock_time": "2026-08-12T05:00:00",
            "ticks": str(638900000000000000 + count),
            "submicrosecond_100ns": 0,
        },
        tier=19,
        wave=wave,
        game_time_seconds=1250,
        real_time_seconds=250,
        killed_by_id=3,
        is_tournament=is_tournament,
        fingerprint=_sha(label),
    )
    return BattleHistoryTail(
        structural_status="unavailable",
        structural_reason="legacy_history_capability_not_declared",
        entry_count=count,
        capacity=30,
        identity=None,
        completed_entry_status="unavailable",
        completed_entry_reason="legacy_history_capability_not_declared",
        entry=None,
        terminal_metric_claims=terminal_metric_claims or {},
        terminal_identity=identity,
        terminal_identity_reason="",
    )


def _semantic_empty_terminal_tail() -> BattleHistoryTail:
    return BattleHistoryTail(
        structural_status="empty",
        structural_reason="battle_history_empty",
        entry_count=0,
        capacity=30,
        identity=None,
        completed_entry_status="not_applicable",
        completed_entry_reason="battle_history_empty",
        entry=None,
        terminal_mapping_id=SEMANTIC_MAPPING_ID,
        terminal_tail_fingerprint=_sha("semantic-empty"),
        terminal_empty_baseline=True,
    )


def _runtime(
    revision: int,
    *,
    offset: int,
    wave: int,
    real: float,
    game: float,
    coins: float,
    cells: float,
    cash: float,
    progress: int,
    coin_source: float,
    resource: float,
    active: bool = True,
) -> NormalizedRuntimeSave:
    identity = ActiveRoundIdentity(
        game_version=1101,
        current_tier=19,
        rounds_started_this_tier=12,
        round_seed=12345,
        fingerprint=_sha("round-1"),
    )
    tallies = _tallies(
        real=real,
        game=game,
        coins=coins,
        cells=cells,
        cash=cash,
        progress=progress,
        coin_source=coin_source,
        resource=resource,
    )
    if not active:
        tallies = ActiveRunTalliesSnapshot(
            status="not_applicable",
            reason="round_inactive",
            state="inactive_round",
            capability_id=CAPABILITY_ID,
            semantic_fingerprint=SEMANTIC_FINGERPRINT,
            binding_fingerprint=BINDING_FINGERPRINT,
            forward_policy="additive_dependencies",
            audit_id="V1101-RUNTIME-017",
            evidence_level="cross_channel",
            components=(),
        )
    return NormalizedRuntimeSave(
        mapping_id="data-9-game-1101",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": (START + timedelta(seconds=offset)).isoformat(),
            "source_sha256": _sha(f"save-{revision}"),
        },
        save_revision=revision,
        round_active=active,
        current_wave=wave,
        active_round_identity=identity if active else None,
        perks_status="unavailable",
        perks_reason="not_under_test",
        perks=None,
        battle_history_tail=_history_tail(),
        active_tallies_status=tallies.status,
        active_tallies_reason=tallies.reason,
        active_tallies=tallies,
    )


def _acquisition(
    runtime: NormalizedRuntimeSave,
    *,
    context: PerkSaveMonitorContext | None = None,
    boundary_activity: str | None = None,
    boundary_identity: str | None = None,
) -> PlayerSaveAcquisitionBundle:
    bound = context or _context()
    captured = datetime.fromisoformat(str(runtime.capture["captured_at"]))
    acquisition_type = (
        PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
        if runtime.round_active
        else PlayerSaveAcquisitionType.NATURAL_BOUNDARY
    )
    boundary = (
        None
        if runtime.round_active
        else PlayerSaveNaturalBoundary(
            PlayerSaveBoundaryKind.GAME_OVER,
            captured - timedelta(milliseconds=100),
            bound.runtime_session_id,
            boundary_activity or bound.activity_scope_id,
            boundary_identity or bound.active_round_identity_fingerprint,
        )
    )
    semantic_forward = runtime.mapping_id == SEMANTIC_MAPPING_ID
    active_identity = runtime.active_round_identity
    snapshot = SimpleNamespace(
        runtime_save=runtime,
        game_version=(
            active_identity.game_version
            if active_identity is not None
            else 1102 if semantic_forward else 1101
        ),
        mapping_id=runtime.mapping_id,
        mapping_resolution=(
            "semantic_forward_revision"
            if semantic_forward
            else "compatible_exact_revision"
        ),
        effective_mapping_fingerprint=_sha("effective-mapping"),
        captured_at=runtime.capture["captured_at"],
        source_sha256=runtime.capture["source_sha256"],
        save_revision=runtime.save_revision,
        shape_valid=True,
        mapping_supported=True,
        capabilities={
            CAPABILITY_ID: SimpleNamespace(
                status=runtime.active_tallies.status,
                semantic_fingerprint=SEMANTIC_FINGERPRINT,
                binding_fingerprint=BINDING_FINGERPRINT,
                resolution=(
                    "semantic_forward_revision"
                    if semantic_forward
                    else "compatible_exact_revision"
                ),
            )
        },
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=acquisition_type,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=bound.target_binding,
        acquisition_started_at=captured - timedelta(milliseconds=50),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=25),
        transport_stable=True,
        snapshot=snapshot,
        boundary=boundary,
    )


def _observe(
    monitor: ActiveRunMetricMonitor,
    runtime: NormalizedRuntimeSave,
    *,
    context: PerkSaveMonitorContext | None = None,
) -> str:
    bound = context or _context()
    acquisition = _acquisition(runtime, context=bound)
    return monitor.observe_bundle(acquisition, context=bound)


def _terminal_runtime() -> NormalizedRuntimeSave:
    return _runtime(
        12,
        offset=250,
        wave=0,
        real=0,
        game=0,
        coins=0,
        cells=0,
        cash=0,
        progress=0,
        coin_source=0,
        resource=0,
        active=False,
    )


def _terminal_report() -> dict:
    direct = {
        "cashEarned": 1000,
        "cellsEarned": 400,
        "coinsEarned": 4000,
        "wavesSkipped": 30,
        "coinsFromBlackHole": 500,
        "rerollShardsEarned": 70,
        "gameTime": 1250,
        "realTime": 250,
    }
    terminal_runtime = _terminal_runtime()
    acquisition = _acquisition(terminal_runtime)
    reference = _tallies(
        real=250,
        game=1250,
        coins=4000,
        cells=400,
        cash=1000,
        progress=30,
        coin_source=500,
        resource=70,
    )
    claim_definitions = {
        metric.terminal_source: metric
        for component in reference.components
        for _name, metric in component.metrics
        if metric.terminal_source is not None
    }
    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "terminal_state": "GAME_OVER",
        "mapping_id": "data-9-game-1101",
        "capture": {
            "captured_at": terminal_runtime.capture["captured_at"],
            "save_revision": terminal_runtime.save_revision,
            "source_fingerprint": terminal_runtime.capture["source_sha256"],
            "acquisition": acquisition.redacted_provenance(),
        },
        "completed_entry": {
            "identity": {
                "wave": 250,
                "game_time_seconds": 1250,
                "real_time_seconds": 250,
            },
            "more_stats": {
                "sections": [
                    {
                        "rows": [
                            {
                                "derivation": "direct",
                                "source_fields": [source],
                                "value_decimal": str(value),
                            }
                            for source, value in direct.items()
                        ]
                    }
                ]
            },
        },
        "terminal_metric_claims": {
            "status": "observed",
            "reason": "",
            "capability_id": CAPABILITY_ID,
            "semantic_fingerprint": SEMANTIC_FINGERPRINT,
            "binding_fingerprint": BINDING_FINGERPRINT,
            "saved_wave": 250,
            "claims": {
                source: {
                    "status": "observed",
                    "value_decimal": str(value),
                    "unit": claim_definitions[source].unit,
                    "semantic_id": claim_definitions[source].semantic_id,
                    "semantic_fingerprint": (
                        claim_definitions[source].semantic_fingerprint
                    ),
                }
                for source, value in direct.items()
            },
            "unavailable": {},
        },
        "ui_fallback": {"required": False, "reason": ""},
    }


def _semantic_terminal_report(
    acquisition: PlayerSaveAcquisitionBundle,
) -> dict:
    return terminal_save_report_from_acquisition(
        acquisition,
        terminal_state="GAME_OVER",
        run_binding={
            "schema_version": 1,
            "status": "bound",
            "activity_scope_run_id": "scope-1",
        },
        activity_scope={"schema_version": 1, "run_id": "scope-1"},
        history_transition={
            "schema_version": 1,
            "status": "unavailable",
            "complete": False,
            "reason": "legacy_history_capability_not_declared",
        },
    )


def _checkpoint(
    revision: int,
    *,
    offset: int,
    wave: int,
    real: float,
    game: float,
    coins: float,
    cells: float,
    cash: float,
    progress: int,
    coin_source: float,
    resource: float,
) -> NormalizedRuntimeSave:
    return _runtime(
        revision,
        offset=offset,
        wave=wave,
        real=real,
        game=game,
        coins=coins,
        cells=cells,
        cash=cash,
        progress=progress,
        coin_source=coin_source,
        resource=resource,
    )


def test_semantic_forward_terminal_relation_uses_capability_local_tail():
    monitor = ActiveRunMetricMonitor()
    active = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    assert active.active_round_identity is not None
    active = replace(
        active,
        mapping_id=SEMANTIC_MAPPING_ID,
        active_round_identity=replace(
            active.active_round_identity,
            game_version=1102,
        ),
        battle_history_tail=_semantic_terminal_tail(
            "semantic-baseline",
            count=4,
            wave=5000,
        ),
    )
    terminal_claims = _terminal_report()["terminal_metric_claims"]
    terminal = replace(
        _terminal_runtime(),
        mapping_id=SEMANTIC_MAPPING_ID,
        battle_history_tail=_semantic_terminal_tail(
            "semantic-terminal",
            count=5,
            wave=250,
            terminal_metric_claims=terminal_claims,
        ),
    )

    assert _observe(monitor, active) == "accepted_checkpoint"
    terminal_acquisition = _acquisition(terminal)
    assert monitor.observe_bundle(
        terminal_acquisition,
        context=_context(),
    ) == "terminal_inactive_observed"
    report = _semantic_terminal_report(terminal_acquisition)
    assert report["status"] == "unavailable"
    assert report["terminal_metric_claims"]["status"] == "observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    assert evidence["status"] == "complete"
    assert evidence["terminal_relation"] == {"status": "bound", "reason": ""}
    assert evidence["terminal"]["status"] == "reconciled"
    assert evidence["terminal"]["components"]["economy"]["matched"][
        "coins_earned"
    ] == "4000"


def test_semantic_forward_empty_history_reconciles_first_terminal():
    monitor = ActiveRunMetricMonitor()
    active = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    assert active.active_round_identity is not None
    active = replace(
        active,
        mapping_id=SEMANTIC_MAPPING_ID,
        active_round_identity=replace(
            active.active_round_identity,
            game_version=1102,
        ),
        battle_history_tail=_semantic_empty_terminal_tail(),
    )
    terminal = replace(
        _terminal_runtime(),
        mapping_id=SEMANTIC_MAPPING_ID,
        battle_history_tail=_semantic_terminal_tail(
            "semantic-first-terminal",
            count=1,
            wave=250,
            terminal_metric_claims=_terminal_report()[
                "terminal_metric_claims"
            ],
        ),
    )

    assert _observe(monitor, active) == "accepted_checkpoint"
    terminal_acquisition = _acquisition(terminal)
    assert monitor.observe_bundle(
        terminal_acquisition,
        context=_context(),
    ) == "terminal_inactive_observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_semantic_terminal_report(
            terminal_acquisition
        ),
    )

    assert evidence["status"] == "complete"
    assert evidence["terminal_relation"]["status"] == "bound"


@pytest.mark.parametrize(
    "mutation",
    ("unchanged_tail", "wrong_count", "wrong_kind", "provenance_mismatch"),
)
def test_semantic_forward_terminal_relation_fails_closed(mutation):
    monitor = ActiveRunMetricMonitor()
    active = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    assert active.active_round_identity is not None
    active = replace(
        active,
        mapping_id=SEMANTIC_MAPPING_ID,
        active_round_identity=replace(
            active.active_round_identity,
            game_version=1102,
        ),
        battle_history_tail=_semantic_terminal_tail(
            "semantic-baseline",
            count=4,
            wave=5000,
        ),
    )
    terminal_label = (
        "semantic-baseline"
        if mutation == "unchanged_tail"
        else "semantic-terminal"
    )
    terminal_count = 6 if mutation == "wrong_count" else 5
    terminal = replace(
        _terminal_runtime(),
        mapping_id=SEMANTIC_MAPPING_ID,
        battle_history_tail=_semantic_terminal_tail(
            terminal_label,
            count=terminal_count,
            wave=250,
            is_tournament=mutation == "wrong_kind",
            terminal_metric_claims=_terminal_report()[
                "terminal_metric_claims"
            ],
        ),
    )

    assert _observe(monitor, active) == "accepted_checkpoint"
    terminal_acquisition = _acquisition(terminal)
    disposition = monitor.observe_bundle(
        terminal_acquisition,
        context=_context(),
    )
    report = _semantic_terminal_report(terminal_acquisition)
    if mutation == "provenance_mismatch":
        assert disposition == "terminal_inactive_observed"
        report["capture"]["source_fingerprint"] = _sha("wrong-source")
    else:
        assert disposition == (
            "terminal_inactive_observed_without_causal_tail"
        )
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    assert evidence["status"] == "retained_checkpoints"
    assert evidence["terminal"]["status"] == "unavailable"


def test_tracks_whole_run_and_interval_rates_then_reconciles_terminal():
    monitor = ActiveRunMetricMonitor()
    first = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    second = _checkpoint(
        11,
        offset=200,
        wave=200,
        real=200,
        game=1000,
        coins=3000,
        cells=300,
        cash=900,
        progress=20,
        coin_source=400,
        resource=60,
    )

    assert _observe(monitor, first) == "accepted_checkpoint"
    assert _observe(monitor, second) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"

    summary = monitor.latest_summary(_context())
    assert summary is not None
    assert summary["saved_wave"] == 200
    assert summary["source_fingerprint"] == second.capture["source_sha256"]
    assert summary["whole_run"] == {
        "real_time_seconds": "200",
        "game_time_seconds": "1000",
        "waves": "200",
        "coins_earned": "3000",
        "cells_earned": "300",
        "cash_earned": "900",
        "coins_per_hour": "54000",
        "cells_per_hour": "5400",
        "cash_per_hour": "16200",
        "waves_per_hour": "3600",
        "effective_game_speed": "5",
    }
    assert summary["interval"] == {
        "real_time_seconds": "100",
        "game_time_seconds": "500",
        "waves": "100",
        "coins_earned": "2000",
        "cells_earned": "200",
        "cash_earned": "400",
        "coins_per_hour": "72000",
        "cells_per_hour": "7200",
        "cash_per_hour": "14400",
        "waves_per_hour": "3600",
        "effective_game_speed": "5",
    }
    performance = monitor.performance_evidence(_context(), limit=3)
    assert performance is not None
    assert performance["mapping_id"]
    assert performance["semantic_fingerprint"]
    assert performance["checkpoints"] == [
        {
            "captured_at": summary["captured_at"],
            "save_revision": summary["save_revision"],
            "saved_wave": 200,
            "interval": summary["interval"],
        }
    ]

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )
    assert evidence["status"] == "complete"
    assert evidence["terminal"]["status"] == "reconciled"
    assert evidence["terminal"]["components"]["economy"][
        "tail_interval"
    ]["coins_per_hour"] == "72000"
    assert evidence["terminal"]["components"]["economy"][
        "whole_run"
    ] == {
        "real_time_seconds": "250",
        "game_time_seconds": "1250",
        "waves": "250",
        "coins_earned": "4000",
        "cells_earned": "400",
        "cash_earned": "1000",
        "coins_per_hour": "57600",
        "cells_per_hour": "5760",
        "cash_per_hour": "14400",
        "waves_per_hour": "3600",
        "effective_game_speed": "5",
    }
    assert evidence["components"]["coin_sources"]["samples"][-1][
        "metrics"
    ]["coins_from_black_hole"] == "400"
    assert evidence["components"]["coin_sources"]["samples"][-1][
        "whole_run"
    ]["per_hour"]["coins_from_black_hole"] == "7200"
    assert evidence["components"]["coin_sources"]["samples"][-1][
        "interval"
    ]["per_hour"]["coins_from_black_hole"] == "10800"
    assert evidence["terminal"]["components"]["coin_sources"][
        "tail_interval"
    ]["per_hour"]["coins_from_black_hole"] == "7200"
    assert evidence["terminal"]["components"]["coin_sources"][
        "whole_run"
    ]["per_hour"]["coins_from_black_hole"] == "7200"


def test_malformed_checkpoint_leaf_does_not_erase_other_or_prior_claims():
    monitor = ActiveRunMetricMonitor()
    first = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    second = _checkpoint(
        11,
        offset=200,
        wave=200,
        real=200,
        game=1000,
        coins=3000,
        cells=300,
        cash=900,
        progress=20,
        coin_source=400,
        resource=60,
    )
    tallies = second.active_tallies
    assert tallies is not None
    components = []
    for component in tallies.components:
        if component.name == "economy":
            component = replace(
                component,
                status="partial",
                reason="one_or_more_tally_claims_unavailable",
                metrics=tuple(
                    item for item in component.metrics if item[0] != "cells_earned"
                ),
                unavailable=(("cells_earned", "source_type_changed"),),
            )
        components.append(component)
    second = replace(
        second,
        active_tallies=replace(
            tallies,
            status="partial",
            reason="one_or_more_active_tally_claims_unavailable",
            components=tuple(components),
        ),
        active_tallies_status="partial",
    )

    assert _observe(monitor, first) == "accepted_checkpoint"
    assert _observe(monitor, second) == "accepted_partial_checkpoint"
    summary = monitor.latest_summary(_context())
    assert summary is not None
    assert summary["whole_run"]["coins_per_hour"] == "54000"
    assert "cells_per_hour" not in summary["whole_run"]
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )
    economy = evidence["components"]["economy"]
    assert economy["status"] == "partial"
    assert "cells_earned" in economy["samples"][0]["metrics"]
    assert "cells_earned" not in economy["samples"][1]["metrics"]
    terminal = evidence["terminal"]["components"]["economy"]
    assert terminal["status"] == "reconciled"
    assert terminal["tail_interval"]["cells_per_hour"] == "7200"


def test_missing_terminal_leaf_only_degrades_its_reconciliation():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["status"] = "partial"
    report["terminal_metric_claims"]["claims"].pop("cellsEarned")
    report["terminal_metric_claims"]["unavailable"] = {
        "cellsEarned": "source_type_changed"
    }

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )
    economy = evidence["terminal"]["components"]["economy"]
    assert economy["status"] == "partial"
    assert economy["missing"] == ["cells_earned"]
    assert economy["matched"]["coins_earned"] == "4000"
    assert economy["tail_interval"]["coins_per_hour"] == "72000"
    assert "cells_per_hour" not in economy["tail_interval"]


def test_leaf_unavailable_at_every_checkpoint_cannot_report_complete():
    monitor = ActiveRunMetricMonitor()
    runtime = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    tallies = runtime.active_tallies
    assert tallies is not None
    runtime = replace(
        runtime,
        active_tallies=replace(
            tallies,
            status="partial",
            reason="one_or_more_active_tally_claims_unavailable",
            components=tuple(
                replace(
                    component,
                    status="partial",
                    reason="one_or_more_tally_claims_unavailable",
                    metrics=tuple(
                        item
                        for item in component.metrics
                        if item[0] != "cells_earned"
                    ),
                    unavailable=(("cells_earned", "source_type_changed"),),
                )
                if component.name == "economy"
                else component
                for component in tallies.components
            ),
        ),
        active_tallies_status="partial",
    )

    assert _observe(monitor, runtime) == "accepted_partial_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )

    assert evidence["status"] == "partial"
    economy = evidence["terminal"]["components"]["economy"]
    assert economy["status"] == "partial"
    assert economy["missing"] == ["cells_earned"]


def test_entire_unavailable_component_retains_expected_terminal_claims():
    monitor = ActiveRunMetricMonitor()
    runtime = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    tallies = runtime.active_tallies
    assert tallies is not None
    runtime = replace(
        runtime,
        active_tallies=replace(
            tallies,
            status="partial",
            reason="one_or_more_active_tally_claims_unavailable",
            components=tuple(
                replace(
                    component,
                    status="unavailable",
                    reason="all_tally_claims_unavailable",
                    metrics=(),
                    derived=(),
                    unavailable=tuple(
                        (name, "source_type_changed")
                        for name, _definition in component.claim_definitions
                    ),
                )
                if component.name == "progress"
                else component
                for component in tallies.components
            ),
        ),
        active_tallies_status="partial",
    )

    assert _observe(monitor, runtime) == "accepted_partial_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )

    assert evidence["status"] == "partial"
    progress = evidence["terminal"]["components"]["progress"]
    assert progress["status"] == "unavailable"
    assert progress["reason"] == "component_checkpoint_unavailable"
    assert evidence["components"]["progress"]["metric_definitions"]


def test_recovered_leaf_interval_uses_its_latest_valid_checkpoint():
    monitor = ActiveRunMetricMonitor()
    first = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    middle = _checkpoint(
        11,
        offset=200,
        wave=200,
        real=200,
        game=1000,
        coins=3000,
        cells=300,
        cash=900,
        progress=20,
        coin_source=400,
        resource=60,
    )
    tallies = middle.active_tallies
    assert tallies is not None
    middle = replace(
        middle,
        active_tallies=replace(
            tallies,
            status="partial",
            reason="one_or_more_active_tally_claims_unavailable",
            components=tuple(
                replace(
                    component,
                    status="partial",
                    reason="one_or_more_tally_claims_unavailable",
                    metrics=tuple(
                        item
                        for item in component.metrics
                        if item[0] != "cells_earned"
                    ),
                    unavailable=(("cells_earned", "source_type_changed"),),
                )
                if component.name == "economy"
                else component
                for component in tallies.components
            ),
        ),
        active_tallies_status="partial",
    )
    recovered = _checkpoint(
        12,
        offset=300,
        wave=300,
        real=300,
        game=1500,
        coins=5000,
        cells=600,
        cash=1300,
        progress=30,
        coin_source=700,
        resource=100,
    )

    assert _observe(monitor, first) == "accepted_checkpoint"
    assert _observe(monitor, middle) == "accepted_partial_checkpoint"
    assert _observe(monitor, recovered) == "accepted_checkpoint"

    summary = monitor.latest_summary(_context())
    assert summary is not None
    interval = summary["interval"]
    assert interval["cells_earned"] == "500"
    assert interval["cells_per_hour"] == "9000"
    assert interval["real_time_seconds_by_metric"]["cells_earned"] == "200"


def test_missing_current_wave_preserves_non_wave_totals_and_rates():
    monitor = ActiveRunMetricMonitor()
    first = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    second = replace(
        _checkpoint(
            11,
            offset=200,
            wave=200,
            real=200,
            game=1000,
            coins=3000,
            cells=300,
            cash=900,
            progress=20,
            coin_source=400,
            resource=60,
        ),
        current_wave=None,
        current_wave_status="unavailable",
        current_wave_reason="currentWave must be a nonnegative integer",
    )

    assert _observe(monitor, first) == "accepted_checkpoint"
    assert _observe(monitor, second) == "accepted_partial_checkpoint"
    summary = monitor.latest_summary(_context())

    assert summary is not None
    assert summary["saved_wave"] is None
    assert summary["whole_run"]["coins_per_hour"] == "54000"
    assert summary["whole_run"]["effective_game_speed"] == "5"
    assert "waves_per_hour" not in summary["whole_run"]
    assert summary["interval"]["coins_per_hour"] == "72000"
    assert "waves_per_hour" not in summary["interval"]


def test_active_wave_regression_does_not_reappear_in_terminal_rates():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(
        monitor,
        _checkpoint(
            11,
            offset=200,
            wave=90,
            real=200,
            game=1000,
            coins=3000,
            cells=300,
            cash=900,
            progress=20,
            coin_source=400,
            resource=60,
        ),
    ) == "accepted_partial_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )

    assert evidence["wave_claim"]["status"] == "conflict"
    economy = evidence["terminal"]["components"]["economy"]
    assert economy["status"] == "partial"
    assert "waves_per_hour" not in economy["whole_run"]
    assert "waves_per_hour" not in economy["tail_interval"]
    assert economy["whole_run"]["coins_per_hour"] == "57600"


def test_real_time_regression_disables_only_transitive_rates():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(
        monitor,
        _checkpoint(
            11,
            offset=180,
            wave=180,
            real=90,
            game=900,
            coins=2000,
            cells=200,
            cash=700,
            progress=18,
            coin_source=300,
            resource=45,
        ),
    ) == "accepted_checkpoint"
    assert _observe(
        monitor,
        _checkpoint(
            12,
            offset=220,
            wave=220,
            real=200,
            game=1100,
            coins=3000,
            cells=300,
            cash=900,
            progress=25,
            coin_source=450,
            resource=60,
        ),
    ) == "accepted_checkpoint"

    summary = monitor.latest_summary(_context())
    assert summary is not None
    assert summary["whole_run"] is None
    assert summary["interval"] is None
    assert summary["components"]["economy"]["latest"]["metrics"][
        "coins_earned"
    ] == "3000"
    assert summary["components"]["coin_sources"]["latest"]["metrics"][
        "coins_from_black_hole"
    ] == "450"
    assert summary["components"]["coin_sources"]["latest"]["whole_run"] is None
    assert summary["components"]["progress"]["latest"]["whole_run"] is None

    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )

    assert evidence["status"] == "partial"
    for component_name in ("economy", "coin_sources", "progress"):
        component = evidence["terminal"]["components"][component_name]
        assert component["status"] == "partial"
        assert "shared_rate_clock_conflict:real_time_seconds" in component[
            "reason"
        ]
        assert "whole_run" not in component
        assert "tail_interval" not in component
        assert component["matched"]


def test_regression_conflicts_only_its_leaf_and_retains_other_timelines():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(
        monitor,
        _checkpoint(
            11,
            offset=200,
            wave=200,
            real=200,
            game=1000,
            coins=900,
            cells=300,
            cash=900,
            progress=20,
            coin_source=400,
            resource=60,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )
    assert evidence["status"] == "partial"
    assert evidence["components"]["economy"]["status"] == "partial"
    assert evidence["components"]["economy"]["metric_conflicts"] == {
        "coins_earned": "monotonic_metric_regressed:coins_earned"
    }
    assert len(evidence["components"]["economy"]["samples"]) == 2
    assert "coins_earned" not in evidence["components"]["economy"][
        "samples"
    ][-1]["metrics"]
    assert len(evidence["components"]["progress"]["samples"]) == 2
    assert evidence["terminal"]["status"] == "partial"
    assert evidence["terminal"]["components"]["economy"]["status"] == (
        "partial"
    )
    assert evidence["terminal"]["components"]["progress"]["status"] == (
        "reconciled"
    )
    terminal_economy = evidence["terminal"]["components"]["economy"]
    assert "coins_per_hour" not in terminal_economy["whole_run"]
    assert "coins_per_hour" not in terminal_economy["tail_interval"]


def test_terminal_regression_excludes_only_that_leaf_from_rates():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["claims"]["coinsEarned"][
        "value_decimal"
    ] = "900"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    economy = evidence["terminal"]["components"]["economy"]
    assert economy["conflicts"]["coins_earned"] == "terminal_metric_regressed"
    assert "coins_per_hour" not in economy["whole_run"]
    assert "coins_per_hour" not in economy["tail_interval"]
    assert economy["whole_run"]["cells_per_hour"] == "5760"


def test_terminal_real_time_regression_disables_all_transitive_rates():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["claims"]["realTime"][
        "value_decimal"
    ] = "50"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    assert evidence["status"] == "partial"
    for component_name in ("economy", "coin_sources", "progress"):
        component = evidence["terminal"]["components"][component_name]
        assert component["status"] == "partial"
        assert "terminal_rate_clock_regressed" in component["reason"]
        assert "whole_run" not in component
        assert "tail_interval" not in component
        assert component["matched"]


@pytest.mark.parametrize("mutation", ("missing", "malformed"))
def test_terminal_real_time_unavailable_is_shared_rate_failure(mutation):
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    claims = report["terminal_metric_claims"]["claims"]
    if mutation == "missing":
        claims.pop("realTime")
    else:
        claims["realTime"]["value_decimal"] = "malformed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    assert evidence["status"] == "partial"
    for component_name in ("economy", "coin_sources", "progress"):
        component = evidence["terminal"]["components"][component_name]
        assert component["status"] == "partial"
        assert "terminal_rate_clock_unavailable" in component["reason"]
        assert "whole_run" not in component
        assert "tail_interval" not in component
        assert component["matched"]


def test_regressed_terminal_wave_degrades_only_wave_rates():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["saved_wave"] = 50

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    assert evidence["status"] == "partial"
    assert evidence["terminal"]["wave_claim"]["status"] == "conflict"
    assert evidence["terminal"]["wave_claim"]["reason"] == (
        "terminal_wave_regressed"
    )
    economy = evidence["terminal"]["components"]["economy"]
    assert economy["status"] == "partial"
    assert "waves_per_hour" not in economy["whole_run"]
    assert "waves_per_hour" not in economy["tail_interval"]
    assert economy["whole_run"]["coins_per_hour"] == "57600"


def test_malformed_terminal_leaf_value_preserves_sibling_reconciliation():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["claims"]["cellsEarned"][
        "value_decimal"
    ] = "malformed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    economy = evidence["terminal"]["components"]["economy"]
    assert economy["status"] == "partial"
    assert economy["matched"]["coins_earned"] == "4000"
    assert economy["missing"] == ["cells_earned"]
    assert economy["claim_issues"]["cells_earned"] == (
        "terminal_claim_value_invalid"
    )


def test_terminal_leaf_contract_mismatch_preserves_siblings():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    report = _terminal_report()
    report["terminal_metric_claims"]["claims"]["cellsEarned"][
        "semantic_fingerprint"
    ] = _sha("wrong-leaf-contract")

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=report,
    )

    economy = evidence["terminal"]["components"]["economy"]
    assert economy["matched"]["coins_earned"] == "4000"
    assert economy["missing"] == ["cells_earned"]
    assert economy["claim_issues"]["cells_earned"] == (
        "terminal_claim_contract_mismatch"
    )


def test_duplicate_source_is_ignored_but_identity_mutation_conflicts_components():
    monitor = ActiveRunMetricMonitor()
    runtime = _checkpoint(
        10,
        offset=100,
        wave=100,
        real=100,
        game=500,
        coins=1000,
        cells=100,
        cash=500,
        progress=10,
        coin_source=100,
        resource=20,
    )
    assert _observe(monitor, runtime) == "accepted_checkpoint"
    assert _observe(monitor, runtime) == "ignored_duplicate_checkpoint"

    changed = _checkpoint(
        10,
        offset=101,
        wave=101,
        real=101,
        game=505,
        coins=1100,
        cells=101,
        cash=501,
        progress=10,
        coin_source=100,
        resource=20,
    )
    assert _observe(monitor, changed) == "no_component_checkpoint_accepted"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"
    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report=_terminal_report(),
    )
    assert evidence["components"]["economy"]["status"] == "partial"
    assert evidence["components"]["progress"]["status"] == "observed"
    assert evidence["components"]["coin_sources"]["status"] == "observed"


def test_save_revision_is_diagnostic_when_later_evidence_is_monotonic():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(
        monitor,
        _checkpoint(
            9,
            offset=200,
            wave=200,
            real=200,
            game=1000,
            coins=3000,
            cells=300,
            cash=900,
            progress=20,
            coin_source=400,
            resource=60,
        ),
    ) == "accepted_checkpoint"

    summary = monitor.latest_summary(_context())
    assert summary is not None
    assert summary["save_revision"] == 9


def test_unavailable_terminal_report_retains_bound_active_checkpoints():
    monitor = ActiveRunMetricMonitor()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
    ) == "accepted_checkpoint"
    assert _observe(monitor, _terminal_runtime()) == "terminal_inactive_observed"

    evidence = monitor.terminal_evidence(
        context=_context(),
        terminal_save_report={"complete": False},
    )

    assert evidence["status"] == "retained_checkpoints"
    assert evidence["terminal"]["status"] == "unavailable"
    assert evidence["terminal"]["reason"] == "terminal_save_report_unavailable"
    assert len(evidence["components"]["economy"]["samples"]) == 1


def test_terminal_window_rejects_a_different_activity_scope():
    monitor = ActiveRunMetricMonitor()
    context = _context()
    assert _observe(
        monitor,
        _checkpoint(
            10,
            offset=100,
            wave=100,
            real=100,
            game=500,
            coins=1000,
            cells=100,
            cash=500,
            progress=10,
            coin_source=100,
            resource=20,
        ),
        context=context,
    ) == "accepted_checkpoint"
    acquisition = _acquisition(
        _terminal_runtime(),
        context=context,
        boundary_identity="b" * 64,
    )

    assert monitor.observe_bundle(acquisition, context=context) == (
        "rejected_unbound_terminal_boundary"
    )
    evidence = monitor.terminal_evidence(
        context=context,
        terminal_save_report=_terminal_report(),
    )
    assert evidence["status"] == "retained_checkpoints"
    assert evidence["terminal"]["reason"] == (
        "terminal_checkpoint_window_unbound"
    )


def test_new_activity_resets_evidence_but_target_change_does_not_rebind():
    monitor = ActiveRunMetricMonitor()
    assert monitor.bind_context(_context())
    assert monitor.bind_context(_context(activity="scope-2"))
    assert monitor.bind_context(
        _context(activity="scope-2", identity="b" * 64),
        new_activity=True,
    )
    assert not monitor.bind_context(
        _context(activity="scope-3", generation=5, identity="c" * 64),
        new_activity=True,
    )
