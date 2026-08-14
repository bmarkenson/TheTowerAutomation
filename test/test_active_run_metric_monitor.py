from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

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
    NormalizedRuntimeSave,
    RuntimeTallyComponent,
    RuntimeTallyMetric,
)


UTC = timezone.utc
START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _context(*, activity: str = "scope-1", generation: int = 4):
    return PerkSaveMonitorContext(
        runtime_session_id="runtime-1",
        activity_scope_id=activity,
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
    return ActiveRunTalliesSnapshot(
        status="observed",
        reason="",
        state="active_round",
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
        )
    )
    snapshot = SimpleNamespace(
        runtime_save=runtime,
        game_version=1101,
        mapping_id=runtime.mapping_id,
        shape_valid=True,
        mapping_supported=True,
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
    }
    terminal_runtime = _terminal_runtime()
    acquisition = _acquisition(terminal_runtime)
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
        "ui_fallback": {"required": False, "reason": ""},
    }


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


def test_regression_conflicts_only_its_component_and_retains_other_timelines():
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
    assert evidence["components"]["economy"]["status"] == "conflict"
    assert len(evidence["components"]["economy"]["samples"]) == 1
    assert len(evidence["components"]["progress"]["samples"]) == 2
    assert evidence["terminal"]["status"] == "conflict"
    assert evidence["terminal"]["reason"] == "terminal_component_conflict:economy"
    assert evidence["terminal"]["components"]["progress"]["status"] == (
        "reconciled"
    )


def test_duplicate_source_is_ignored_but_mutation_conflicts_components():
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
    assert all(
        component["status"] == "conflict"
        for component in evidence["components"].values()
    )


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
        boundary_activity="scope-other",
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
    assert monitor.bind_context(_context(activity="scope-2"), new_activity=True)
    assert not monitor.bind_context(
        _context(activity="scope-3", generation=5),
        new_activity=True,
    )
