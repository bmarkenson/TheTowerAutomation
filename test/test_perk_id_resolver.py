from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from core.perk_id_resolver import (
    normalize_timeline_mapping_batch,
    resolve_runtime_perk_ids,
    timeline_family_to_perk_key,
)
from core.runtime_save import (
    BattleHistoryTail,
    NormalizedRuntimeSave,
    RuntimePerkCalibration,
    RuntimePerkCalibrationPick,
)


UTC = timezone.utc


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime(
    picks: tuple[RuntimePerkCalibrationPick, ...],
    *,
    known: tuple[tuple[int, str], ...] = ((10, "perk_wave_requirement"),),
) -> NormalizedRuntimeSave:
    levels: dict[int, int] = {}
    for pick in picks:
        levels[pick.perk_id] = pick.level_after
    calibration = RuntimePerkCalibration(
        state="active_round",
        picked_count=len(picks),
        levels=tuple(sorted(levels.items())),
        picks=picks,
        known_ids=known,
        fingerprint=_sha("structural-calibration"),
    )
    return NormalizedRuntimeSave(
        mapping_id="data-9-game-1073",
        audit_matrix_id="data-9-game-1073-runtime-audit-v2",
        capture={
            "captured_at": datetime(2026, 8, 3, tzinfo=UTC).isoformat(),
            "source_sha256": _sha("save"),
        },
        save_revision=1,
        round_active=True,
        current_wave=max(pick.wave for pick in picks),
        active_round_identity=None,
        perks_status="unavailable",
        perks_reason=f"unmapped_perk_id:{picks[0].perk_id}",
        perks=None,
        battle_history_tail=BattleHistoryTail(
            structural_status="empty",
            structural_reason="battle_history_empty",
            entry_count=0,
            capacity=30,
            identity=None,
            completed_entry_status="not_applicable",
            completed_entry_reason="battle_history_empty",
            entry=None,
        ),
        perk_calibration=calibration,
    )


def _pick(sequence: int, wave: int, perk_id: int, level: int = 1):
    return RuntimePerkCalibrationPick(sequence, wave, perk_id, level)


def _batch(
    wave: int,
    *families: str,
    sequence: int = 1,
    confidence: float = 95.0,
) -> dict:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "scheduled_wave": wave,
        "scheduled_waves": [wave],
        "boundary_coverage": "complete",
        "selection_model": "simultaneous_batch",
        "observed_at": datetime(2026, 8, 3, 12, sequence, tzinfo=UTC).isoformat(),
        "selections": [
            {
                "family": family,
                "confidence_percent": confidence,
                "change": "added",
            }
            for family in families
        ],
    }


def test_unique_unknown_id_restores_complete_semantic_projection():
    runtime = _runtime((_pick(1, 200, 11),))

    result = resolve_runtime_perk_ids(runtime, [_batch(200, "interest")], {})

    assert result.overrides == {11: "interest"}
    assert result.unresolved_ids == ()
    assert result.conflicts == ()
    assert len(result.learned) == 1
    assert result.learned[0].perk_id == 11
    assert result.learned[0].perk_key == "interest"
    assert result.runtime.perks_status == "observed"
    assert result.runtime.perks is not None
    assert result.runtime.perks.picks[0].perk_key == "interest"


def test_known_assignments_are_cancelled_before_resolving_unknown_id():
    runtime = _runtime((_pick(1, 200, 10), _pick(2, 200, 11)))

    result = resolve_runtime_perk_ids(
        runtime,
        [_batch(200, "perk_wave_requirement", "interest")],
        {},
    )

    assert result.overrides == {11: "interest"}
    assert result.runtime.perks is not None
    assert [pick.perk_key for pick in result.runtime.perks.picks] == [
        "perk_wave_requirement",
        "interest",
    ]


def test_overlapping_exact_batches_can_prove_an_otherwise_ambiguous_pair():
    runtime = _runtime(
        (
            _pick(1, 200, 11),
            _pick(2, 200, 15),
            _pick(3, 300, 11, 2),
        )
    )

    result = resolve_runtime_perk_ids(
        runtime,
        [
            _batch(200, "interest", "defense_absolute"),
            _batch(300, "interest", sequence=2),
        ],
        {},
    )

    assert result.overrides == {11: "interest", 15: "defense_absolute"}
    assert result.unresolved_ids == ()
    assert result.runtime.perks is not None


def test_ambiguous_batch_keeps_entire_perk_projection_unavailable():
    runtime = _runtime((_pick(1, 200, 11), _pick(2, 200, 15)))

    result = resolve_runtime_perk_ids(
        runtime,
        [_batch(200, "interest", "defense_absolute")],
        {},
    )

    assert result.overrides == {}
    assert result.learned == ()
    assert result.unresolved_ids == (11, 15)
    assert result.runtime.perks is None
    assert result.runtime.perks_status == "unavailable"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda batch: batch.update(boundary_coverage="partial"),
        lambda batch: batch.update(selection_model="interval_aggregate"),
        lambda batch: batch.update(scheduled_waves=[200, 300]),
        lambda batch: batch.update(observed_at="2026-08-03T12:00:00"),
        lambda batch: batch["selections"][0].update(confidence_percent=79.9),
        lambda batch: batch["selections"][0].update(family="unknown"),
    ),
)
def test_inexact_or_low_confidence_batches_do_not_map(mutation):
    runtime = _runtime((_pick(1, 200, 11),))
    batch = _batch(200, "interest")
    mutation(batch)

    result = resolve_runtime_perk_ids(runtime, [batch], {})

    assert result.overrides == {}
    assert result.runtime.perks is None


def test_later_exact_conflict_invalidates_a_session_mapping():
    runtime = _runtime((_pick(1, 200, 11),))

    result = resolve_runtime_perk_ids(
        runtime,
        [_batch(200, "defense_absolute")],
        {11: "interest"},
    )

    assert result.conflicts == (11,)
    assert result.overrides == {}
    assert result.runtime.perks is None


def test_invalid_or_duplicate_overrides_are_ignored_without_crashing():
    runtime = _runtime((_pick(1, 200, 11),))

    result = resolve_runtime_perk_ids(
        runtime,
        (),
        {
            "11": "interest",
            12: "perk_wave_requirement",
            13: "bad key",
        },
    )

    assert result.overrides == {}
    assert result.unresolved_ids == (11,)
    assert result.runtime.perks is None


@pytest.mark.parametrize(
    ("timeline_family", "perk_key"),
    (
        ("all_coins_bonuses", "coins_bonus"),
        ("cash_wave_tradeoff", "cash_tradeoff"),
        ("inner_mines", "inner_land_mines"),
        ("max_game_speed", "game_speed"),
        ("wave_on_death", "death_wave_quantity"),
    ),
)
def test_timeline_family_aliases_match_save_vocabulary(
    timeline_family,
    perk_key,
):
    assert timeline_family_to_perk_key(timeline_family) == perk_key


def test_prequeue_normalizer_rejects_display_text_and_emits_only_allowlist():
    batch = _batch(200, "interest")
    batch["selections"][0]["display_text"] = "private unbounded text"
    assert normalize_timeline_mapping_batch(batch) is None

    batch = _batch(200, "interest")
    normalized = normalize_timeline_mapping_batch(batch)

    assert normalized is not None
    rendered = repr(normalized).lower()
    assert "display_text" not in rendered
    assert "private" not in rendered
    assert normalized["selections"] == [
        {
            "family": "interest",
            "confidence_percent": 95.0,
            "change": "added",
        }
    ]

    arbitrary = _batch(200, "operator_notes_disguised_as_family")
    assert normalize_timeline_mapping_batch(arbitrary) is None
