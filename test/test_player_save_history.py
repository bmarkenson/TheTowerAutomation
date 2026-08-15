from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.player_save import SaveCheckEvidence
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_history import (
    CrossSourceHistoryStatus,
    PlayerSaveAttachmentContext,
    corroborate_ui_and_save_history,
    history_metadata_from_acquisition,
    history_sources_compatible,
    running_attachment_observations_from_acquisition,
    running_attachment_temporal_binding_from_acquisition,
    valid_history_tail_advance,
)
from core.player_save_temporal import PlayerSaveTemporalClass


def _save_battle_date(
    *,
    kind: str = "local",
    clock_time: str = "2026-07-15T01:41:37.123456",
):
    kinds = {
        "unspecified": (0, "unspecified", clock_time),
        "utc": (1, "utc", f"{clock_time}+00:00"),
        "local": (2, "local_wall_clock_without_offset", clock_time),
        "local_ambiguous": (
            3,
            "local_wall_clock_without_offset",
            clock_time,
        ),
    }
    kind_id, clock_basis, normalized_time = kinds[kind]
    return {
        "kind_id": kind_id,
        "kind": kind,
        "ticks": "639197340971234560",
        "clock_time": normalized_time,
        "clock_basis": clock_basis,
        "submicrosecond_100ns": 0,
    }


def _snapshot(
    *,
    fingerprint: str = "a" * 64,
    entry_count: int = 29,
    capacity: int = 30,
    semantic_status: str = "observed",
    semantic_reason: str = "",
    active: bool = True,
    battle_date=None,
    profile_checks=None,
):
    identity = SimpleNamespace(
        mapping_id="data-9-game-1073",
        fingerprint=fingerprint,
        tier=19,
        wave=1899,
        is_tournament=False,
        battle_date=battle_date or _save_battle_date(),
    )
    tail = SimpleNamespace(
        structural_status="observed",
        structural_reason="",
        identity=identity,
        entry_count=entry_count,
        capacity=capacity,
        completed_entry_status=semantic_status,
        completed_entry_reason=semantic_reason,
    )
    runtime = SimpleNamespace(
        mapping_id="data-9-game-1073",
        battle_history_tail=tail,
        round_active=active,
        active_round_identity=(
            SimpleNamespace(
                game_version=1073,
                current_tier=19,
                rounds_started_this_tier=12,
                round_seed=123456789,
                fingerprint="active-round-fingerprint",
            )
            if active
            else None
        ),
    )
    return SimpleNamespace(
        runtime_save=runtime,
        captured_at="2026-08-04T20:00:00+00:00",
        game_version=1073,
        mapping_id="data-9-game-1073",
        effective_mapping_fingerprint="9" * 64,
        mapping_maturity="candidate",
        validated_checks=tuple((profile_checks or {}).keys()),
        shape_valid=True,
        checks=dict(profile_checks or {}),
    )


def _attachment_context(**changes):
    values = {
        "runtime_session_id": "runtime-1",
        "activity_scope_id": "scope-1",
        "active_round_identity_fingerprint": "active-round-fingerprint",
        "target": "private-target",
        "target_generation": 3,
        "active_battle_observed": True,
    }
    values.update(changes)
    return PlayerSaveAttachmentContext(**values)


def _acquisition(
    snapshot,
    acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
):
    started = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    captured = started + timedelta(milliseconds=1)
    return PlayerSaveAcquisitionBundle(
        acquisition_type=acquisition_type,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="save_acquired",
        binding=PlayerSaveTargetBinding("private-target", 3),
        acquisition_started_at=started,
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(milliseconds=1),
        transport_stable=True,
        snapshot=snapshot,
    )


def test_structural_tail_remains_authoritative_when_semantics_are_unavailable():
    result = history_metadata_from_acquisition(
        _acquisition(_snapshot(
            semantic_status="unavailable",
            semantic_reason="unmapped_killed_by_id:999",
        )),
    )

    assert result.complete
    assert result.metadata is not None
    assert result.metadata["source"] == "player_save"
    assert result.metadata["mapping_id"] == "data-9-game-1073"
    assert result.metadata["semantic_status"] == "unavailable"
    assert result.metadata["semantic_reason"] == "unmapped_killed_by_id:999"
    assert result.metadata["battle_date"] == _save_battle_date()
    assert "killed_by_id" not in result.metadata


def _ui_history_metadata(**changes):
    values = {
        "schema_version": 2,
        "source": "battle_history_ui",
        "mapping_id": "battle-history-ui-report-v1",
        "identity_schema_version": 1,
        "fingerprint": "ui-fingerprint-never-compared",
        "battle_date": "Jul 15, 2026 01:41",
        "tier": "19",
        "wave": "1899",
    }
    values.update(changes)
    return values


def _save_history_metadata(**snapshot_changes):
    result = history_metadata_from_acquisition(
        _acquisition(_snapshot(**snapshot_changes)),
    )
    assert result.metadata is not None
    return dict(result.metadata)


def test_cross_source_bridge_matches_tier_wave_and_local_date_without_fingerprint():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        _save_history_metadata(fingerprint="save-fingerprint-differs"),
    )

    assert result.status is CrossSourceHistoryStatus.MATCH
    assert result.reason == "cross_source_tier_wave_battle_date_match"


@pytest.mark.parametrize(
    ("ui_changes", "save_changes", "reason"),
    (
        (
            {"tier": "18"},
            {},
            "cross_source_tier_wave_mismatch",
        ),
        (
            {"wave": "1900"},
            {},
            "cross_source_tier_wave_mismatch",
        ),
        (
            {"battle_date": "Jul 15, 2026 01:42"},
            {},
            "cross_source_battle_date_mismatch",
        ),
    ),
)
def test_cross_source_bridge_reports_shared_identity_mismatch(
    ui_changes,
    save_changes,
    reason,
):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(**ui_changes),
        _save_history_metadata(**save_changes),
    )

    assert result.status is CrossSourceHistoryStatus.MISMATCH
    assert result.reason == reason


@pytest.mark.parametrize(
    "kind",
    ("utc", "unspecified", "local_ambiguous"),
)
def test_cross_source_bridge_rejects_ambiguous_save_date_kind(kind):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        _save_history_metadata(battle_date=_save_battle_date(kind=kind)),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "cross_source_battle_date_ambiguous"


def test_cross_source_bridge_rejects_malformed_ui_date():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(battle_date="2026-07-15 01:41 unknown-zone"),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


def test_cross_source_bridge_rejects_unknown_ui_identity_schema():
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(identity_schema_version=2),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tier", 0),
        ("wave", "0"),
        ("tier", False),
        ("wave", -1),
        ("tier", "01"),
        ("wave", " 1899"),
        ("tier", "+19"),
        ("wave", "1899.0"),
    ),
)
def test_cross_source_bridge_rejects_nonpositive_or_noncanonical_ui_values(
    field,
    value,
):
    result = corroborate_ui_and_save_history(
        _ui_history_metadata(**{field: value}),
        _save_history_metadata(),
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "ui_history_identity_insufficient"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tier", 0),
        ("wave", "0"),
        ("tier", True),
        ("wave", -1),
        ("tier", "01"),
        ("wave", " 1899"),
        ("tier", "+19"),
        ("wave", "1899.0"),
    ),
)
def test_cross_source_bridge_rejects_nonpositive_or_noncanonical_save_values(
    field,
    value,
):
    save_metadata = _save_history_metadata()
    save_metadata[field] = value

    result = corroborate_ui_and_save_history(
        _ui_history_metadata(),
        save_metadata,
    )

    assert result.status is CrossSourceHistoryStatus.AMBIGUOUS
    assert result.reason == "save_history_tier_wave_ambiguous"

def test_running_attachment_binding_uses_forced_save_battle_identity():
    context = _attachment_context()
    acquisition = _acquisition(_snapshot())

    binding = running_attachment_temporal_binding_from_acquisition(
        acquisition,
        context=context,
        active_round_identity_fingerprint=(
            context.active_round_identity_fingerprint
        ),
    )

    assert binding is not None
    assert binding.runtime_session_id == "runtime-1"
    assert binding.source_activity_scope_id == "scope-1"
    assert binding.activity_scope_id is None
    assert binding.active_round_identity_fingerprint == (
        "active-round-fingerprint"
    )
    assert binding.target_binding == PlayerSaveTargetBinding(
        "private-target",
        3,
    )


@pytest.mark.parametrize(
    "changed_identity",
    ("different-battle", ""),
)
def test_running_attachment_binding_rejects_missing_or_changed_battle_identity(
    changed_identity,
):
    context = _attachment_context()

    assert (
        running_attachment_temporal_binding_from_acquisition(
            _acquisition(_snapshot()),
            context=context,
            active_round_identity_fingerprint=changed_identity,
        )
        is None
    )


def test_running_attachment_projection_keeps_only_validated_save_facts():
    snapshot = _snapshot(
        profile_checks={
            "cards_deck": SaveCheckEvidence(
                check_id="cards_deck",
                status="observed",
                value="Farm",
                source_fields=("presetName", "currentPreset"),
            ),
            "unvalidated_check": SaveCheckEvidence(
                check_id="unvalidated_check",
                status="observed",
                value="private candidate",
                source_fields=("unvalidatedField",),
            ),
        }
    )
    snapshot.validated_checks = ("cards_deck",)
    context = _attachment_context()

    observations = running_attachment_observations_from_acquisition(
        _acquisition(snapshot),
        context=context,
        active_round_identity_fingerprint=(
            context.active_round_identity_fingerprint
        ),
    )

    assert observations is not None
    assert observations.binding.active_round_identity_fingerprint == (
        "active-round-fingerprint"
    )
    assert len(observations.facts) == 1
    fact = observations.facts[0]
    assert fact.check_id == "cards_deck"
    assert fact.temporal_class is PlayerSaveTemporalClass.POINT_IN_TIME
    assert fact.copied_value() == "Farm"
    assert fact.source_fields == ("presetName", "currentPreset")

def test_source_compatibility_and_capacity_rollover_are_explicit():
    previous = history_metadata_from_acquisition(
        _acquisition(_snapshot(fingerprint="a" * 64, entry_count=30)),
    ).metadata
    rollover = history_metadata_from_acquisition(
        _acquisition(
            _snapshot(fingerprint="b" * 64, entry_count=30),
            PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
        ),
    ).metadata
    append = history_metadata_from_acquisition(
        _acquisition(
            _snapshot(fingerprint="c" * 64, entry_count=30),
            PlayerSaveAcquisitionType.PASSIVE_STABLE_READ,
        ),
    ).metadata
    assert previous is not None and rollover is not None and append is not None

    assert history_sources_compatible(previous, rollover)
    assert valid_history_tail_advance(previous, rollover)
    optional_leaf_correction = {
        **previous,
        "game_time_seconds": 9000,
        "real_time_seconds": 1800,
        "killed_by_id": 7,
    }
    assert not valid_history_tail_advance(previous, optional_leaf_correction)

    wrong_source = {**rollover, "source": "battle_history_ui"}
    assert not history_sources_compatible(previous, wrong_source)
    assert not valid_history_tail_advance(previous, wrong_source)

    before_append = {**previous, "entry_count": 29}
    assert valid_history_tail_advance(before_append, append)
    assert not valid_history_tail_advance(
        before_append,
        {**append, "entry_count": 29},
    )
