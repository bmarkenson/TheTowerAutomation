from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from core.battle_perks import ocr_latest_selected_perk
from core.perk_timeline import (
    PerkCaptureRequest,
    PerkProgress,
    PerkTimelineObserver,
    PerkTimelineTracker,
    _perk_activity_data,
    _perk_selection_alias,
    _recorded_selection_summary,
    measure_perk_progress,
    timeline_perk_family,
)
from core.scrolling import ScrollCaptureResult, ScrollResult


FIXTURES = Path(__file__).parent / "fixtures"


def _progress(current: int, next_wave: int) -> PerkProgress:
    return PerkProgress(
        "scheduled",
        current,
        next_wave,
        f"{current} / {next_wave}",
        90.0,
    )


def _perk(
    display_text: str,
    *,
    color: str = "blue",
    confidence: float = 95.0,
) -> dict:
    return {
        "display_text": display_text,
        "color": color,
        "instance_model": (
            "leveled" if color == "blue" else "single_instance"
        ),
        "confidence": confidence,
    }


def _full(*perks: dict) -> dict:
    return {
        "selected": list(perks),
        "quality": {
            "source_complete": True,
            "valid": True,
        },
    }


def _saved_checkpoint(revision: int, saved_wave: int, *picks: tuple) -> dict:
    normalized = []
    levels = {}
    for sequence, (wave, perk_id, perk_key, level_after) in enumerate(
        picks,
        start=1,
    ):
        normalized.append(
            {
                "sequence": sequence,
                "saved_wave": wave,
                "perk_id": perk_id,
                "perk_key": perk_key,
                "level_after": level_after,
                "source": "exact_saved_pick",
            }
        )
        levels[perk_id] = (perk_key, level_after)
    return {
        "schema_version": 1,
        "mapping_id": "data-9-game-1101",
        "audit_matrix_id": "data-9-game-1101-runtime-audit-v2",
        "game_version": 1101,
        "save_revision": revision,
        "saved_wave": saved_wave,
        "captured_at": datetime.fromtimestamp(
            1_800_000_000 + revision,
            tz=timezone.utc,
        ).isoformat(),
        "active_round_identity": {
            "game_version": 1101,
            "current_tier": 19,
            "rounds_started_this_tier": 7,
            "round_seed": 12345,
            "fingerprint": hashlib.sha256(b"round").hexdigest(),
        },
        "complete": True,
        "picked_count": len(normalized),
        "order_semantics": "oldest_selected_first_exact_saved_order",
        "picks": normalized,
        "levels": [
            {"perk_id": perk_id, "perk_key": key, "level": level}
            for perk_id, (key, level) in sorted(levels.items())
        ],
        "prefix_fingerprint": hashlib.sha256(
            repr(normalized).encode("utf-8")
        ).hexdigest(),
        "acceptance": "strict_prefix_extension",
        "acquisition_type": "passive_stable_read",
    }


def _stabilize(
    tracker: PerkTimelineTracker,
    progress: PerkProgress,
    *,
    wave: int,
):
    tracker.observe(progress, wave=wave)
    return tracker.observe(progress, wave=wave)


def test_saved_prefix_replaces_panel_timeline_and_extends_exactly():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=False)
    _stabilize(tracker, _progress(500, 540), wave=500)
    assert tracker.pending is not None

    first = _saved_checkpoint(
        10,
        520,
        (100, 0, "max_health", 1),
        (200, 1, "perk_wave_requirement", 1),
        (300, 1, "perk_wave_requirement", 2),
    )
    assert tracker.record_saved_checkpoint(first) == "initial_saved_prefix"
    assert tracker.pending is None
    assert tracker.drain_mapping_evidence() == ()

    extended = _saved_checkpoint(
        11,
        620,
        (100, 0, "max_health", 1),
        (200, 1, "perk_wave_requirement", 1),
        (300, 1, "perk_wave_requirement", 2),
        (540, 1, "perk_wave_requirement", 3),
        (580, 2, "damage", 1),
    )
    assert tracker.record_saved_checkpoint(extended) == (
        "strict_saved_prefix_extension"
    )

    snapshot = tracker.snapshot()
    assert snapshot["source"] == "player_save_perk_prefix_with_passive_top_bar"
    assert snapshot["baseline_status"] == "save_backed_mid_battle"
    assert snapshot["pwr_maxed_observed"] is True
    assert snapshot["save_backed_prefix"]["picked_count"] == 5
    assert [
        batch["selections"][0]["perk_key"]
        for batch in snapshot["batches"]
    ] == [
        "max_health",
        "perk_wave_requirement",
        "perk_wave_requirement",
        "perk_wave_requirement",
        "damage",
    ]
    assert all(
        batch["selection_model"] == "exact_saved_pick"
        and batch["snapshot_mode"] == "player_save_checkpoint"
        for batch in snapshot["batches"]
    )
    assert tracker.drain_mapping_evidence() == ()


def test_current_perks_presentation_collapses_levels_newest_first():
    tracker = PerkTimelineTracker()
    assert tracker.current_perks_presentation() == {
        "schema_version": 1,
        "source": "monitor_validated_player_save_perk_prefix",
        "order_semantics": "most_recent_selection_first",
        "status": "awaiting_save_checkpoint",
        "reason": "save_checkpoint_unavailable",
        "captured_at": None,
        "saved_wave": None,
        "picked_count": 0,
        "unique_count": 0,
        "items": [],
    }

    checkpoint = _saved_checkpoint(
        12,
        420,
        (100, 0, "max_health", 1),
        (200, 1, "perk_wave_requirement", 1),
        (250, 1, "perk_wave_requirement", 2),
        (300, 1, "perk_wave_requirement", 3),
        (400, 2, "damage", 1),
    )
    assert tracker.record_saved_checkpoint(checkpoint) == "initial_saved_prefix"

    presentation = tracker.current_perks_presentation()
    assert presentation["status"] == "available"
    assert presentation["captured_at"] == checkpoint["captured_at"]
    assert presentation["saved_wave"] == 420
    assert presentation["picked_count"] == 5
    assert presentation["unique_count"] == 3
    assert presentation["items"] == [
        {
            "perk_key": "damage",
            "label": "Damage",
            "level": 1,
            "last_selected_wave": 400,
            "last_selected_sequence": 5,
        },
        {
            "perk_key": "perk_wave_requirement",
            "label": "Perk Wave Requirement",
            "level": 3,
            "last_selected_wave": 300,
            "last_selected_sequence": 4,
        },
        {
            "perk_key": "max_health",
            "label": "Max Health",
            "level": 1,
            "last_selected_wave": 100,
            "last_selected_sequence": 1,
        },
    ]


def test_passive_observation_never_opens_perks_for_a_pending_baseline():
    tracker = PerkTimelineTracker(confirmation_frames=1)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)

    with (
        patch(
            "core.perk_timeline.safe_tap",
            side_effect=AssertionError("passive monitoring must not tap"),
        ),
        patch(
            "core.perk_timeline.swipe_now",
            side_effect=AssertionError("passive monitoring must not swipe"),
        ),
    ):
        observer.observe_passive(
            running,
            {"state": "RUNNING"},
            wave=500,
            progress_fn=lambda _frame: _progress(500, 540),
        )

    assert tracker.pending is not None
    assert tracker.pending.kind == "baseline"


def test_save_backed_timeline_checkpoint_restores_for_the_same_scope(tmp_path):
    state_path = tmp_path / "perk-timeline.json"
    observer = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-save-backed-battle",
    )
    checkpoint = _saved_checkpoint(
        12,
        300,
        (100, 0, "max_health", 1),
        (200, 1, "damage", 1),
    )
    assert observer.observe_saved_checkpoint(checkpoint) == "initial_saved_prefix"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["current_perks"] == (
        observer.tracker.current_perks_presentation()
    )

    restarted = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-save-backed-battle",
    )

    assert restarted.snapshot() == observer.snapshot()
    assert restarted.snapshot()["save_backed_prefix"]["picked_count"] == 2


def _mapping_request(*waves: int) -> PerkCaptureRequest:
    return PerkCaptureRequest(
        kind="selection",
        scheduled_wave=waves[0],
        observed_wave=waves[-1] + 1,
        progress_after=_progress(waves[-1], waves[-1] + 42),
        snapshot_mode="until_first_unchanged",
        scheduled_waves=tuple(waves),
        observed_wave_end=waves[-1] + 1,
    )


def test_mapping_evidence_drain_is_allowlisted_and_exactly_once():
    tracker = PerkTimelineTracker()
    tracker._append_batch(
        _mapping_request(200),
        [
            {
                "family": "interest",
                "confidence": 95.0,
                "change": "added",
                "display_text": "Interest +hidden value",
                "before_display_text": "must not cross",
                "color": "green",
            }
        ],
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    evidence = tracker.drain_mapping_evidence()

    assert evidence == (
        {
            "schema_version": 1,
            "sequence": 1,
            "scheduled_wave": 200,
            "scheduled_waves": [200],
            "boundary_coverage": "complete",
            "selection_model": "simultaneous_batch",
            "observed_at": "2026-08-03T12:00:00+00:00",
            "selections": [
                {
                    "family": "interest",
                    "confidence_percent": 95.0,
                    "change": "added",
                }
            ],
        },
    )
    assert tracker.drain_mapping_evidence() == ()


def test_mapping_evidence_reset_and_checkpoint_restore_never_replay():
    source = PerkTimelineTracker()
    source._append_batch(
        _mapping_request(200),
        [{"family": "interest", "confidence": 95.0, "change": "added"}],
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    checkpoint = source.checkpoint()

    restored = PerkTimelineTracker()
    assert restored.restore_checkpoint(checkpoint) is True
    assert restored.drain_mapping_evidence() == ()

    source.reset(fresh_battle=True)
    assert source.drain_mapping_evidence() == ()


def test_mapping_evidence_uses_existing_semantic_classifier_before_redaction():
    tracker = PerkTimelineTracker()
    tracker._append_batch(
        _mapping_request(200),
        [
            {
                "family": "x1_defense_absolute",
                "confidence": 95.0,
                "change": "added",
                "display_text": "x1.44 Defense Absolute",
            }
        ],
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    evidence = tracker.drain_mapping_evidence()

    assert evidence[0]["selections"][0]["family"] == "defense_absolute"
    assert "display_text" not in repr(evidence)


def test_reconstructed_singletons_emit_independent_exact_mapping_batches():
    tracker = PerkTimelineTracker()
    tracker._append_ordered_post_pwr_batches(
        _mapping_request(200, 242),
        [
            {"family": "orbs", "confidence": 96.0, "change": "added"},
            {"family": "interest", "confidence": 95.0, "change": "added"},
        ],
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    evidence = tracker.drain_mapping_evidence()

    assert [batch["scheduled_wave"] for batch in evidence] == [200, 242]
    assert [batch["selections"][0]["family"] for batch in evidence] == [
        "interest",
        "orbs",
    ]
    assert {
        batch["selection_model"] for batch in evidence
    } == {"singleton_after_pwr_max_reconstructed"}


def test_recorded_selection_summary_uses_singular_for_one_perk():
    assert _recorded_selection_summary(["Bounce Shot +2"]) == (
        "Perk timeline selection recorded — Bounce Shot +2"
    )


def test_recorded_selection_summary_uses_plural_for_multiple_perks():
    assert _recorded_selection_summary(
        ["Bounce Shot +2", "Orbs +1"]
    ) == (
        "Perk timeline selections recorded — Bounce Shot +2, Orbs +1"
    )


def test_recorded_selection_summary_announces_all_perks_selected():
    assert _recorded_selection_summary(
        ["Orbs +1"],
        all_selected=True,
        scheduled_waves=[100],
    ) == "All Perks selected at wave 100 — final selection: Orbs +1"


def test_perk_selection_aliases_use_familiar_community_names():
    assert _perk_selection_alias(
        "Perk wave requirement -75.00%"
    ) == "PWR"
    assert _perk_selection_alias(
        "x1.98 coins, but tower max health -70.0%"
    ) == "CTO"
    assert _perk_selection_alias(
        "tower health regen x8.80, but tower max health -60%"
    ) == "RTO"
    assert _perk_selection_alias(
        "Enemies damage -55.0%, but tower damage -50%"
    ) == "50/50"
    assert _perk_selection_alias("Golden tower bonus x1.5") == "GT"
    assert _perk_selection_alias("Black Hole duration +12.0s") == "BH"


def test_perk_activity_data_preserves_exact_bundled_item_boundaries():
    marker = _perk_activity_data(
        [
            "x1.98 coins, but tower max health -70.0%",
            "Perk wave requirement -75.00%",
        ]
    )

    assert marker.startswith(" [ACTIVITY_DATA] ")
    assert '"alias":"CTO"' in marker
    assert '"alias":"PWR"' in marker
    assert "x1.98 coins, but tower max health -70.0%" in marker


def test_observer_announces_all_perks_after_terminal_capture():
    tracker = PerkTimelineTracker()
    terminal = PerkProgress("complete", None, None, "View Perks", 95.0)
    request = PerkCaptureRequest(
        kind="selection",
        scheduled_wave=100,
        observed_wave=101,
        progress_after=terminal,
        snapshot_mode="full",
        scheduled_waves=(100,),
        observed_wave_end=101,
    )
    tracker._pending = request
    tracker._batches = [
        {
            "selections": [
                {"display_text": "Orbs +1"},
            ]
        }
    ]
    observer = PerkTimelineObserver(tracker)
    observer._route_open = True
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)

    with (
        patch.object(
            observer,
            "_capture_pending",
            return_value=(True, request),
        ),
        patch(
            "core.perk_timeline._close_perks_panel",
            return_value=SimpleNamespace(
                dispatched=True,
                closed=True,
                observed_state="RUNNING",
            ),
        ),
        patch("core.perk_timeline.log_action_intent") as action_log,
        patch("core.perk_timeline.log_result") as result_log,
    ):
        navigated = observer.handle(
            panel,
            {"state": "PERKS"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=lambda: True,
            capture_fn=lambda: panel,
        )

    assert navigated is True
    assert result_log.call_args.args[0] == (
        "All Perks selected at wave 100 — final selection: Orbs +1"
    )
    assert result_log.call_args.kwargs["operation_id"] == (
        action_log.call_args.kwargs["operation_id"]
    )


def test_measure_perk_progress_reads_retained_dynamic_top_bar():
    frame = cv2.imread(
        str(FIXTURES / "open_perks_dynamic_progress_20260723.png")
    )
    assert frame is not None

    progress = measure_perk_progress(frame)

    assert progress.status == "scheduled"
    assert progress.current_wave == 80
    assert progress.next_wave == 191


def test_measure_perk_progress_reads_retained_view_perks_top_bar():
    frame = cv2.imread(
        str(FIXTURES / "open_perks_complete_20260809.png")
    )
    assert frame is not None

    progress = measure_perk_progress(frame)

    assert progress.status == "complete"
    assert progress.text_raw == "View Perks"
    assert progress.confidence >= 80.0


def test_measure_perk_progress_tolerates_ocr_artifacts_and_terminal_label():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    noisy = measure_perk_progress(
        frame,
        text_fn=lambda crop: ("3003)/°3025", 0.0),
    )
    complete = measure_perk_progress(
        frame,
        text_fn=lambda crop: ("View Perks", 93.0),
    )

    assert noisy.current_wave == 3003
    assert noisy.next_wave == 3025
    assert complete.status == "complete"


def _complete_progress(*, confidence: float = 93.0) -> PerkProgress:
    observed_at = "2026-08-07T12:00:00+00:00"
    return PerkProgress(
        "complete",
        None,
        None,
        "View Perks",
        confidence,
        observed_at=observed_at,
        source_fingerprint=hashlib.sha256(b"view-perks").hexdigest(),
    )


def test_stable_view_perks_persists_bound_exhaustion(tmp_path):
    state_path = tmp_path / "perk-timeline.json"
    observer = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-battle",
    )
    observer.reset(fresh_battle=True)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    progress = _complete_progress()

    for _ in range(2):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=700,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: progress,
        ) is False

    evidence = observer.exhaustion_evidence()
    assert evidence is not None
    assert evidence["source"] == "stable_top_bar_view_perks"
    assert evidence["activity_scope_id"] == "same-battle"
    assert evidence["observed_wave"] == 700
    assert evidence["stable_observation_count"] == 2
    assert evidence["binding_status"] == "pending_active_round_identity"
    assert observer.tracker.pending is None

    identity = {
        "game_version": 1073,
        "current_tier": 22,
        "rounds_started_this_tier": 9,
        "round_seed": 12345,
        "fingerprint": hashlib.sha256(b"active-round").hexdigest(),
    }
    assert observer.bind_exhaustion_identity(identity)
    evidence = observer.exhaustion_evidence()
    assert evidence["binding_status"] == "active_round_identity_bound"
    assert evidence["active_round_identity"] == identity

    restarted = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-battle",
    )
    assert restarted.exhaustion_evidence() == evidence


def test_unstable_or_low_confidence_view_perks_is_not_exhaustion(tmp_path):
    observer = PerkTimelineObserver(
        state_path=tmp_path / "perk-timeline.json",
        scope_id_fn=lambda: "same-battle",
    )
    observer.reset(fresh_battle=True)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)

    observer.handle(
        running,
        {"state": "RUNNING"},
        wave=700,
        actions_allowed=False,
        action_guard_fn=lambda: False,
        progress_fn=lambda frame: _complete_progress(),
    )
    observer.handle(
        running,
        {"state": "RUNNING"},
        wave=700,
        actions_allowed=False,
        action_guard_fn=lambda: False,
        progress_fn=lambda frame: PerkProgress(
            "unreadable", None, None, "", -1.0
        ),
    )
    assert observer.exhaustion_evidence() is None

    for _ in range(3):
        observer.handle(
            running,
            {"state": "RUNNING"},
            wave=700,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: _complete_progress(confidence=20.0),
        )
    assert observer.exhaustion_evidence() is None


def test_measure_perk_progress_rejects_unreconciled_concatenated_wave():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    progress = measure_perk_progress(
        frame,
        text_fn=lambda crop: ("690 / 7705", 89.0),
    )

    assert progress.status == "invalid_schedule"
    assert progress.current_wave == 690
    assert progress.next_wave == 7705
    assert progress.token is None


def test_observer_reconciles_prefixed_next_wave_with_independent_wave():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    contaminated = PerkProgress(
        "invalid_schedule",
        3089,
        773124,
        "3089)/773124",
        41.0,
    )

    for _ in range(2):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=3089,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: contaminated,
        ) is False

    assert tracker.checkpoint()["armed_next_wave"] == 3124


def test_observer_reconciles_top_bar_current_with_independent_wave():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    contaminated = PerkProgress(
        "invalid_schedule",
        31227,
        3124,
        "31227 / 3124",
        89.0,
    )

    with patch("core.perk_timeline.log") as log_mock:
        for _ in range(2):
            assert observer.handle(
                running,
                {"state": "RUNNING"},
                wave=3122,
                actions_allowed=False,
                action_guard_fn=lambda: False,
                progress_fn=lambda frame: contaminated,
            ) is False

    assert tracker.checkpoint()["armed_next_wave"] == 3124
    assert not any(
        "Ignoring implausible top-bar schedule" in str(call.args[0])
        for call in log_mock.call_args_list
    )


def test_observer_rejects_top_bar_schedule_that_actual_wave_has_passed():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    stale = _progress(690, 705)

    with patch("core.perk_timeline.log") as log_mock:
        for _ in range(3):
            assert observer.handle(
                running,
                {"state": "RUNNING"},
                wave=706,
                actions_allowed=False,
                action_guard_fn=lambda: False,
                progress_fn=lambda frame: stale,
            ) is False

    assert tracker.checkpoint()["armed_next_wave"] is None
    assert any(
        call.args[1] == "WARN"
        and "retrying without device input" in call.args[0]
        for call in log_mock.call_args_list
    )


def test_tracker_requires_the_armed_wave_before_accepting_a_transition():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(690, 705), wave=690)

    assert _stabilize(tracker, _progress(690, 752), wave=690) is None

    request = _stabilize(tracker, _progress(705, 752), wave=705)
    assert request is not None
    assert request.scheduled_wave == 705


def test_tracker_resynchronizes_an_implausibly_distant_armed_wave():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(690, 705), wave=690)
    tracker._armed_next_wave = 7705

    assert _stabilize(tracker, _progress(720, 752), wave=720) is None
    snapshot = tracker.snapshot()
    assert "resynchronized at 752" in snapshot["warnings"][0]

    request = _stabilize(tracker, _progress(752, 799), wave=752)
    assert request is not None
    assert request.scheduled_wave == 752


def test_observer_retries_persistent_invalid_progress_without_navigation():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    invalid = PerkProgress(
        "invalid_schedule",
        690,
        999999,
        "690 / 999999",
        89.0,
    )
    taps = []

    with patch("core.perk_timeline.log") as log_mock:
        for _ in range(3):
            assert observer.handle(
                running,
                {"state": "RUNNING"},
                wave=690,
                actions_allowed=True,
                action_guard_fn=lambda: True,
                progress_fn=lambda frame: invalid,
                safe_tap_fn=lambda *args, **kwargs: taps.append(args) or True,
            ) is False

    assert taps == []
    assert tracker.pending is None
    assert any(
        call.args[1] == "WARN"
        and "retrying without device input" in call.args[0]
        for call in log_mock.call_args_list
    )


def test_tracker_records_pwr_cascades_as_atomic_batches_then_singletons():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    observed_at = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    assert _stabilize(tracker, _progress(80, 100), wave=80) is None
    first = _stabilize(tracker, _progress(100, 142), wave=101)
    assert first is not None
    assert first.scheduled_wave == 100
    assert first.snapshot_mode == "until_first_unchanged"
    assert tracker.record_full_snapshot(
        _full(
            _perk("Perk wave requirement -50.00%"),
            _perk("Increase max game speed by +0.50"),
        ),
        observed_at=observed_at,
    )

    second = _stabilize(tracker, _progress(142, 184), wave=143)
    assert second is not None
    assert tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
            _perk("Increase max game speed by +0.50"),
        ),
        observed_at=observed_at,
    )
    assert tracker.pwr_maxed

    third = _stabilize(tracker, _progress(184, 226), wave=185)
    assert third is not None
    assert third.snapshot_mode == "until_first_unchanged"
    assert tracker.record_snapshot_to_unchanged(
        _full(
            _perk("Defense percent +10.00%"),
            _perk("Perk wave requirement -75.00%"),
        ),
        observed_at=observed_at,
    )

    snapshot = tracker.snapshot()
    assert len(snapshot["batches"]) == 3
    assert snapshot["batches"][0]["selection_model"] == "simultaneous_batch"
    assert {
        selection["family"]
        for selection in snapshot["batches"][0]["selections"]
    } == {"perk_wave_requirement", "max_game_speed"}
    assert {
        selection["family"]
        for selection in snapshot["batches"][1]["selections"]
    } == {"defense_percent", "perk_wave_requirement"}
    assert snapshot["batches"][2]["selection_model"] == (
        "singleton_after_pwr_max"
    )
    assert snapshot["batches"][2]["selections"][0]["before_display_text"] == (
        "Defense percent +5.00%"
    )


def test_paused_observer_coalesces_boundaries_and_arms_latest_progress():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    first = _stabilize(tracker, _progress(100, 142), wave=101)
    assert first is not None
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)

    for progress, wave in (
        (_progress(142, 184), 143),
        (_progress(142, 184), 143),
        (_progress(184, 226), 185),
        (_progress(184, 226), 185),
    ):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=wave,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame, value=progress: value,
        ) is False

    request = tracker.pending
    assert request is not None
    assert request.scheduled_waves == (100, 142, 184)
    assert request.progress_after.next_wave == 226
    assert request.observed_wave == 101
    assert request.observed_wave_end == 185
    assert request.snapshot_mode == "until_first_unchanged"

    assert tracker.record_full_snapshot(
        _full(
            _perk("Perk wave requirement -75.00%"),
            _perk("Defense percent +5.00%"),
            _perk("Increase max game speed by +0.50"),
        )
    )
    batch = tracker.latest_batch
    assert batch is not None
    assert batch["selection_model"] == "interval_aggregate"
    assert batch["scheduled_wave"] == 100
    assert batch["scheduled_waves"] == [100, 142, 184]
    assert batch["observed_wave"] == 101
    assert batch["observed_wave_end"] == 185
    assert "without per-wave attribution" in tracker.snapshot()["warnings"][0]

    next_request = _stabilize(tracker, _progress(226, 268), wave=227)
    assert next_request is not None
    assert next_request.scheduled_wave == 226


def test_deferred_post_pwr_snapshot_reconstructs_newest_first_singletons():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    assert tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )

    request = _stabilize(tracker, _progress(142, 184), wave=143)
    assert request is not None
    assert request.snapshot_mode == "until_first_unchanged"
    _stabilize(tracker, _progress(184, 226), wave=185)

    request = tracker.pending
    assert request is not None
    assert request.scheduled_waves == (142, 184)
    assert request.snapshot_mode == "until_first_unchanged"
    assert tracker.record_snapshot_to_unchanged(
        _full(
            _perk("x1.15 all coins bonuses"),
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
        )
    )
    batches = tracker.snapshot()["batches"]
    assert [batch["scheduled_wave"] for batch in batches] == [100, 142, 184]
    assert [batch["selections"][0]["family"] for batch in batches[-2:]] == [
        "defense_percent",
        "all_coins_bonuses",
    ]
    assert all(
        batch["selection_model"]
        == "singleton_after_pwr_max_reconstructed"
        for batch in batches[-2:]
    )
    assert tracker.snapshot()["warnings"] == []


def test_deferred_post_pwr_repeated_family_remains_interval_aggregate():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    assert tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
        )
    )

    _stabilize(tracker, _progress(142, 184), wave=143)
    _stabilize(tracker, _progress(184, 226), wave=185)
    assert tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +15.00%"),
            _perk("Perk wave requirement -75.00%"),
        )
    )

    assert tracker.latest_batch["selection_model"] == "interval_aggregate"
    assert "distinct changes did not match" in tracker.snapshot()["warnings"][0]


def test_hidden_ui_gap_forces_full_interval_snapshot_without_false_wave():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    assert tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)

    assert observer.handle(
        running,
        {"state": "CARDS"},
        wave=120,
        actions_allowed=False,
        action_guard_fn=lambda: False,
    ) is False
    for _ in range(2):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=184,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: _progress(184, 226),
        ) is False

    request = tracker.pending
    assert request is not None
    assert request.scheduled_wave == 142
    assert request.scheduled_waves == (142,)
    assert request.snapshot_mode == "until_first_unchanged"
    assert request.boundary_coverage == "incomplete_visibility_gap"
    assert tracker.record_full_snapshot(
        _full(
            _perk("x1.15 all coins bonuses"),
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
        )
    )

    batch = tracker.latest_batch
    assert batch is not None
    assert batch["scheduled_wave"] == 142
    assert batch["selection_model"] == "interval_aggregate"
    assert batch["boundary_coverage"] == "incomplete_visibility_gap"
    assert {
        selection["family"] for selection in batch["selections"]
    } == {"all_coins_bonuses", "defense_percent"}
    assert "top-bar schedule was not observable" in tracker.snapshot()[
        "warnings"
    ][0]


def test_catch_up_scans_past_changed_latest_row_to_first_unchanged_row():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    assert tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
            _perk("x1.15 all coins bonuses"),
        )
    )

    tracker.observe(
        _progress(310, 352),
        wave=310,
        boundary_observation_complete=False,
    )
    request = tracker.observe(
        _progress(310, 352),
        wave=310,
        boundary_observation_complete=False,
    )
    assert request is not None
    assert request.scheduled_wave == 142

    assert tracker.record_snapshot_to_unchanged(
        _full(
            _perk("Defense percent +15.00%"),
            _perk("Orbs +1", color="green"),
            _perk("Increase max game speed by +1.00"),
            _perk("Perk wave requirement -75.00%"),
        )
    )

    batch = tracker.latest_batch
    assert batch is not None
    assert batch["selection_model"] == "interval_aggregate"
    assert {
        selection["family"] for selection in batch["selections"]
    } == {"defense_percent", "orbs", "max_game_speed"}
    checkpoint = tracker.checkpoint()
    assert "all_coins_bonuses" in checkpoint["selected_by_family"]
    assert checkpoint["selected_by_family"]["defense_percent"][
        "display_text"
    ] == "Defense percent +15.00%"


def test_short_hidden_ui_gap_clears_after_same_schedule_is_confirmed():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    assert tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)

    observer.handle(
        running,
        {"state": "CARDS"},
        wave=120,
        actions_allowed=False,
        action_guard_fn=lambda: False,
    )
    for _ in range(2):
        observer.handle(
            running,
            {"state": "RUNNING"},
            wave=120,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: _progress(120, 142),
        )

    request = _stabilize(tracker, _progress(142, 184), wave=143)
    assert request is not None
    assert request.snapshot_mode == "until_first_unchanged"
    assert request.boundary_coverage == "complete"


def test_same_scope_checkpoint_restores_timeline_and_owned_panel(tmp_path):
    state_path = tmp_path / "perk-timeline.json"
    scope = {"run_id": "same-battle"}
    observer = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: scope["run_id"],
    )
    observer.reset(fresh_battle=True)
    _stabilize(observer.tracker, _progress(80, 100), wave=80)
    _stabilize(observer.tracker, _progress(100, 142), wave=101)
    assert observer.tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    observer._route_open = True
    observer._persist_state()

    restarted = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: scope["run_id"],
    )

    assert restarted.snapshot() == observer.snapshot()
    assert restarted.tracker.pwr_maxed is True
    assert restarted.tracker.pending is None
    assert restarted._route_open is True
    assert restarted._progress_visibility_interrupted is True


def test_same_scope_restart_catches_up_across_unobserved_selections(tmp_path):
    state_path = tmp_path / "perk-timeline.json"
    observer = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-battle",
    )
    observer.reset(fresh_battle=True)
    _stabilize(observer.tracker, _progress(80, 100), wave=80)
    _stabilize(observer.tracker, _progress(100, 142), wave=101)
    assert observer.tracker.record_full_snapshot(
        _full(
            _perk("Defense percent +5.00%"),
            _perk("Perk wave requirement -75.00%"),
        )
    )
    observer._persist_state()

    restarted = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: "same-battle",
    )
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    for _ in range(2):
        restarted.handle(
            running,
            {"state": "RUNNING"},
            wave=310,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=lambda frame: _progress(310, 352),
        )

    request = restarted.tracker.pending
    assert request is not None
    assert request.scheduled_wave == 142
    assert request.snapshot_mode == "until_first_unchanged"
    assert request.boundary_coverage == "incomplete_visibility_gap"


def test_checkpoint_from_different_scope_is_not_restored(tmp_path):
    state_path = tmp_path / "perk-timeline.json"
    scope = {"run_id": "old-battle"}
    observer = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: scope["run_id"],
    )
    observer.reset(fresh_battle=True)
    _stabilize(observer.tracker, _progress(80, 100), wave=80)
    _stabilize(observer.tracker, _progress(100, 142), wave=101)
    assert observer.tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    observer._persist_state()

    scope["run_id"] = "new-battle"
    restarted = PerkTimelineObserver(
        state_path=state_path,
        scope_id_fn=lambda: scope["run_id"],
    )

    snapshot = restarted.snapshot()
    assert snapshot["baseline_status"] == "not_observed"
    assert snapshot["batches"] == []
    assert snapshot["pwr_maxed_observed"] is False


def test_mid_battle_attachment_establishes_baseline_without_inventing_waves():
    tracker = PerkTimelineTracker()

    request = _stabilize(tracker, _progress(500, 540), wave=500)

    assert request is not None
    assert request.kind == "baseline"
    assert request.scheduled_wave is None
    assert tracker.record_full_snapshot(
        _full(_perk("Perk wave requirement -75.00%"))
    )
    snapshot = tracker.snapshot()
    assert snapshot["baseline_status"] == "observed_mid_battle"
    assert snapshot["batches"] == []
    assert snapshot["pwr_maxed_observed"] is True


def test_mid_battle_baseline_repeats_after_crossing_scheduled_wave():
    tracker = PerkTimelineTracker()
    initial = _progress(500, 540)
    advanced = _progress(545, 580)
    request = _stabilize(tracker, initial, wave=500)
    assert request is not None
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    screen = {"state": "RUNNING"}
    progress = {"value": initial}
    capture_rounds = []
    open_kwargs = []

    def capture():
        return panel if screen["state"] == "PERKS" else running

    def open_panel(key, **kwargs):
        open_kwargs.append(kwargs)
        screen["state"] = "PERKS"
        return True

    def close_panel(key, **kwargs):
        screen["state"] = "RUNNING"
        return True

    def fake_scroll_to_edge(*args, **kwargs):
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        capture_rounds.append(len(capture_rounds) + 1)
        if len(capture_rounds) == 1:
            progress["value"] = advanced
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
    ):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=500,
            actions_allowed=True,
            action_guard_fn=lambda: True,
            progress_fn=lambda frame: progress["value"],
            capture_fn=capture,
            detector=lambda frame: {"state": screen["state"]},
            safe_tap_fn=open_panel,
            tap_visible_fn=close_panel,
            swipe_fn=lambda key: True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -75.00%")
            ),
            sleep_fn=lambda seconds: None,
        )

    assert capture_rounds == [1, 2]
    assert open_kwargs[0]["failure_log_level"] == "DEBUG"
    assert tracker.pending is None
    assert tracker.snapshot()["baseline_status"] == "observed_mid_battle"
    assert _stabilize(tracker, advanced, wave=545) is None
    next_request = _stabilize(tracker, _progress(581, 625), wave=581)
    assert next_request is not None
    assert next_request.scheduled_wave == 580


def test_latest_perk_reader_uses_top_complete_row_and_color():
    frame = cv2.imread(
        str(
            FIXTURES
            / "ui_state_20260714"
            / "active_perks_selected_auto_pick_on.png"
        )
    )
    assert frame is not None

    latest = ocr_latest_selected_perk(
        frame,
        text_fn=lambda crop: ("Bounce Shot +2", 98.0),
    )

    assert latest is not None
    assert latest["display_text"] == "Bounce Shot +2"
    assert latest["color"] == "blue"
    assert latest["latest_selection_rank"] == 1


def test_family_mapping_distinguishes_enemy_health_from_regen_tradeoff():
    assert timeline_perk_family(
        "Enemies have -55.0% health, but tower health regen and lifesteal -90%"
    ) == "enemy_health_tradeoff"
    assert timeline_perk_family(
        "tower health regen x8.80, but tower max health -60%"
    ) == "tower_health_regen_tradeoff"


def test_observer_guards_each_panel_input_and_records_complete_batch():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guards = []
    taps = []
    screen = {"state": "RUNNING"}

    def guard():
        guards.append("guard")
        return True

    def capture():
        return panel if screen["state"] == "PERKS" else running

    def open_panel(key, **kwargs):
        taps.append(key)
        screen["state"] = "PERKS"
        return True

    def close_panel(key, **kwargs):
        taps.append(key)
        screen["state"] = "RUNNING"
        return True

    def fake_scroll_to_edge(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_top:perks")
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_next:perks")
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
        patch("core.perk_timeline.log_action_intent") as action_log,
        patch("core.perk_timeline.log_result") as result_log,
    ):
        navigated = observer.handle(
            running,
            {"state": "RUNNING"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=guard,
            progress_fn=lambda frame: _progress(100, 142),
            capture_fn=capture,
            detector=lambda frame: {"state": screen["state"]},
            safe_tap_fn=open_panel,
            tap_visible_fn=close_panel,
            swipe_fn=lambda key: taps.append(key) or True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -25.00%")
            ),
            sleep_fn=lambda seconds: None,
        )

    assert navigated is True
    assert taps == [
        "navigation.open_perks",
        "gesture_targets.goto_top:perks",
        "gesture_targets.goto_next:perks",
        "buttons.close:perks",
    ]
    assert len(guards) == 4
    assert tracker.snapshot()["batches"][0]["scheduled_wave"] == 100
    assert action_log.call_args.kwargs["operation_id"]
    assert result_log.call_args.kwargs["operation_id"] == (
        action_log.call_args.kwargs["operation_id"]
    )


def test_observer_leaves_owned_panel_open_when_pause_arrives_before_swipe():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guard_results = iter((True, False))
    closed = []

    navigated = observer.handle(
        running,
        {"state": "RUNNING"},
        wave=101,
        actions_allowed=True,
        action_guard_fn=lambda: next(guard_results),
        progress_fn=lambda frame: _progress(100, 142),
        capture_fn=lambda: panel,
        detector=lambda frame: {"state": "PERKS"},
        safe_tap_fn=lambda key, **kwargs: True,
        tap_visible_fn=lambda key, **kwargs: closed.append(key) or True,
        visible_fn=lambda key, **kwargs: True,
        swipe_fn=lambda key: True,
        sleep_fn=lambda seconds: None,
    )

    assert navigated is True
    assert closed == []
    assert tracker.pending is not None
    assert (
        observer.handle(
            panel,
            {"state": "PERKS"},
            wave=101,
            actions_allowed=False,
            action_guard_fn=lambda: False,
        )
        is False
    )


def test_observer_restores_panel_after_capture_completed_during_pause():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    guard_results = iter((True, True, True, False))
    closed = []
    screen = {"state": "RUNNING"}

    def capture():
        return panel if screen["state"] == "PERKS" else running

    def open_panel(key, **kwargs):
        screen["state"] = "PERKS"
        return True

    def close_panel(key, **kwargs):
        closed.append(key)
        screen["state"] = "RUNNING"
        return True

    def fake_scroll_to_edge(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_top:perks")
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        assert kwargs["swipe_fn"]("gesture_targets.goto_next:perks")
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
    ):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=lambda: next(guard_results),
            progress_fn=lambda frame: _progress(100, 142),
            capture_fn=capture,
            detector=lambda frame: {"state": screen["state"]},
            safe_tap_fn=open_panel,
            tap_visible_fn=close_panel,
            swipe_fn=lambda key: True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -25.00%")
            ),
            sleep_fn=lambda seconds: None,
        )

    assert tracker.pending is None
    assert closed == []
    assert observer.handle(
        panel,
        {"state": "PERKS"},
        wave=101,
        actions_allowed=True,
        action_guard_fn=lambda: True,
        capture_fn=capture,
        detector=lambda frame: {"state": screen["state"]},
        tap_visible_fn=close_panel,
        sleep_fn=lambda seconds: None,
    )
    assert closed == ["buttons.close:perks"]


def test_observer_retains_route_ownership_until_close_transition_is_verified():
    tracker = PerkTimelineTracker()
    tracker.reset(fresh_battle=True)
    _stabilize(tracker, _progress(80, 100), wave=80)
    _stabilize(tracker, _progress(100, 142), wave=101)
    observer = PerkTimelineObserver(tracker)
    running = np.zeros((1920, 1080, 3), dtype=np.uint8)
    panel = np.ones((1920, 1080, 3), dtype=np.uint8)
    screen = {"state": "RUNNING"}
    close_attempts = []

    def capture():
        return panel if screen["state"] == "PERKS" else running

    def open_panel(key, **kwargs):
        screen["state"] = "PERKS"
        return True

    def close_panel(key, **kwargs):
        close_attempts.append(key)
        if len(close_attempts) == 2:
            screen["state"] = "RUNNING"
        return True

    def fake_scroll_to_edge(*args, **kwargs):
        return ScrollResult(True, panel, 1, "edge_reached")

    def fake_capture_scroll(*args, **kwargs):
        return ScrollCaptureResult(True, (panel,), 1, "edge_reached")

    with (
        patch(
            "core.perk_timeline.scroll_to_edge",
            side_effect=fake_scroll_to_edge,
        ),
        patch(
            "core.perk_timeline.capture_scroll_to_edge",
            side_effect=fake_capture_scroll,
        ),
    ):
        assert observer.handle(
            running,
            {"state": "RUNNING"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=lambda: True,
            progress_fn=lambda frame: _progress(100, 142),
            capture_fn=capture,
            detector=lambda frame: {"state": screen["state"]},
            safe_tap_fn=open_panel,
            tap_visible_fn=close_panel,
            swipe_fn=lambda key: True,
            full_ocr_fn=lambda *args, **kwargs: _full(
                _perk("Perk wave requirement -50.00%")
            ),
            sleep_fn=lambda seconds: None,
        )
        assert tracker.pending is None
        assert screen["state"] == "PERKS"

        assert observer.handle(
            panel,
            {"state": "PERKS"},
            wave=101,
            actions_allowed=True,
            action_guard_fn=lambda: True,
            capture_fn=capture,
            detector=lambda frame: {"state": screen["state"]},
            tap_visible_fn=close_panel,
            sleep_fn=lambda seconds: None,
        )

    assert close_attempts == [
        "buttons.close:perks",
        "buttons.close:perks",
    ]
    assert screen["state"] == "RUNNING"
