"""Run-scoped perk selection timeline from the top bar and Perks panel."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import cv2
import numpy as np

from core.battle_perks import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ocr_perk_rows,
    ocr_selected_perks,
)
from core.input import safe_tap, swipe_now, tap_if_visible
from core.label_tapper import is_visible
from core.perk_configuration import (
    classify_perk_configuration_text,
    perk_configuration_label,
)
from core.run_perk_selector import canonical_perk_family
from core.scrolling import capture_scroll_to_edge, scroll_to_edge
from core.ss_capture import capture_adb_screenshot
from core.state_detector import detect_state_and_overlays
from utils.logger import (
    get_activity_scope,
    log,
    log_action_intent,
    log_result,
    new_operation_id,
)
from utils.ocr_utils import ocr_text_and_conf


Frame = np.ndarray
Capture = Callable[[], Optional[Frame]]
Detector = Callable[[Frame], Mapping[str, Any]]
PERKS_INDICATOR = "indicators.perks_panel"
PERKS_CONTENT_REGION = (100, 414, 880, 1340)
PERK_PROGRESS_TEXT_REGION = (400, 25, 340, 71)
# The largest retained real lead is 191 waves. Keep margin for future profiles
# while rejecting separator artifacts such as ``705`` becoming ``7705``.
MAX_PERK_SCHEDULE_LEAD_WAVES = 250
INVALID_PROGRESS_WARNING_FRAMES = 3
PWR_FAMILY = "perk_wave_requirement"
BOUNDARY_COVERAGE_COMPLETE = "complete"
BOUNDARY_COVERAGE_VISIBILITY_GAP = "incomplete_visibility_gap"
SELECTION_SCAN_MODE = "until_first_unchanged"
PERK_TIMELINE_CHECKPOINT_SCHEMA_VERSION = 3
CURRENT_PERKS_PRESENTATION_SCHEMA_VERSION = 1
PERKS_CLOSE_DESTINATIONS = {
    "RUNNING",
    "GAME_OVER",
    "TOURNAMENT_RESULTS",
}
PWR_MAX_PATTERN = re.compile(
    r"perk wave requirement.*-\s*75(?:\.0+)?\s*%",
    re.IGNORECASE,
)
_ACTIVITY_DATA_MARKER = "[ACTIVITY_DATA]"


def _recorded_selection_summary(
    selection_labels: Sequence[str],
    *,
    all_selected: bool = False,
    scheduled_waves: Sequence[int] = (),
) -> str:
    """Describe recorded Perk changes without calling a singleton a batch."""

    wave_values = [int(wave) for wave in scheduled_waves]
    if len(wave_values) == 1:
        wave_context = f" at wave {wave_values[0]}"
    elif wave_values:
        wave_context = (
            " across waves " + ", ".join(str(wave) for wave in wave_values)
        )
    else:
        wave_context = ""
    if all_selected:
        if len(selection_labels) == 1:
            return (
                f"All Perks selected{wave_context} — final selection: "
                f"{selection_labels[0]}"
            )
        if selection_labels:
            return (
                f"All Perks selected{wave_context} — final selections: "
                + ", ".join(selection_labels)
            )
        return f"All Perks selected{wave_context}"
    if wave_context:
        wave_context = wave_context.replace(" at ", " for ", 1)
    if len(selection_labels) == 1:
        return (
            f"Perk timeline selection recorded{wave_context} — "
            f"{selection_labels[0]}"
        )
    if selection_labels:
        return (
            f"Perk timeline selections recorded{wave_context} — "
            + ", ".join(selection_labels)
        )
    return "Perk timeline observation recorded — no selection changes detected"


def _perk_selection_alias(selection_label: str) -> str:
    """Return the familiar compact name used in Perk activity summaries."""

    label = " ".join(str(selection_label or "unknown").split())
    normalized = label.casefold()
    tradeoff_aliases = (
        ("CTO", ("coins", "tower max health")),
        ("RTO", ("tower health regen", "tower max health")),
        ("50/50", ("enemies damage", "tower damage")),
        ("DMG/Boss HP", ("tower damage", "boss", "health")),
        ("Boss HP/Speed", ("boss health", "boss speed")),
        (
            "Enemy HP/Regen",
            ("enemies have", "health", "tower health regen"),
        ),
        ("Enemy Speed/DMG", ("enemies speed", "enemies damage")),
        ("Ranged TO", ("ranged enemies", "attack distance", "damage")),
        ("Cash TO", ("cash per wave", "enemy kills")),
        ("Lifesteal/KB", ("lifesteal", "knockback")),
    )
    for alias, fragments in tradeoff_aliases:
        if all(fragment in normalized for fragment in fragments):
            return alias

    family_aliases = {
        "all_coins_bonuses": "Coins",
        "black_hole_duration": "BH",
        "chain_lightning_damage": "CL",
        "chrono_field_duration": "CF",
        "damage": "DMG",
        "defense_percent": "Def%",
        "free_upgrade_chance": "Free Ups",
        "golden_tower_bonus": "GT",
        "health_regen": "Regen",
        "inner_mines": "ILM",
        "max_game_speed": "GS",
        "max_health": "HP",
        "orbs": "Orbs",
        "perk_wave_requirement": "PWR",
        "smart_missiles": "SM",
        "spotlight_damage_bonus": "SL",
        "swamp_radius": "PS",
        "wave_on_death": "DW",
    }
    family = canonical_perk_family(label)
    return family_aliases.get(family, label)


def _perk_activity_data(selection_labels: Sequence[str]) -> str:
    """Encode exact item boundaries for compact and expanded activity views."""

    normalized = [
        " ".join(str(label or "unknown").split())
        for label in selection_labels
    ]
    if len(normalized) < 2:
        return ""
    payload = {
        "kind": "perk_selection_bundle",
        "items": [
            {
                "alias": _perk_selection_alias(label),
                "label": label,
            }
            for label in normalized
        ],
    }
    return (
        f" {_ACTIVITY_DATA_MARKER} "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


@dataclass(frozen=True)
class PerkProgress:
    """One OCR observation of the compact in-battle perk progress control."""

    status: str
    current_wave: Optional[int]
    next_wave: Optional[int]
    text_raw: str
    confidence: float
    observed_at: Optional[str] = None
    source_fingerprint: Optional[str] = None
    source_region: str = "perk_progress_text"

    @property
    def token(self) -> Optional[tuple[str, Optional[int]]]:
        if (
            self.status == "scheduled"
            and _scheduled_progress_is_plausible(
                self.current_wave,
                self.next_wave,
            )
        ):
            return ("scheduled", self.next_wave)
        if self.status == "complete":
            return ("complete", None)
        return None


@dataclass(frozen=True)
class PerkCaptureRequest:
    """A stable top-bar transition that requires a panel observation."""

    kind: str
    scheduled_wave: Optional[int]
    observed_wave: Optional[int]
    progress_after: PerkProgress
    snapshot_mode: str
    scheduled_waves: tuple[int, ...] = ()
    observed_wave_end: Optional[int] = None
    boundary_coverage: str = BOUNDARY_COVERAGE_COMPLETE


@dataclass(frozen=True)
class _PanelCloseResult:
    """Outcome of one verified attempt to leave the owned Perks panel."""

    dispatched: bool
    closed: bool
    observed_state: str


class PerkTimelineTracker:
    """Turn stable progress changes and panel reads into atomic selection batches."""

    def __init__(self, *, confirmation_frames: int = 2) -> None:
        if confirmation_frames < 1:
            raise ValueError("confirmation_frames must be positive")
        self._confirmation_frames = int(confirmation_frames)
        self.reset(fresh_battle=False)

    def reset(self, *, fresh_battle: bool = True) -> None:
        """Begin a new run, or an unknown mid-run attachment."""

        self._fresh_battle = bool(fresh_battle)
        self._baseline_status = (
            "new_battle_empty" if fresh_battle else "not_observed"
        )
        self._snapshot_known = bool(fresh_battle)
        self._selected_by_family: dict[str, dict[str, Any]] = {}
        self._pwr_maxed = False
        self._batches: list[dict[str, Any]] = []
        self._selection_boundaries: list[dict[str, Any]] = []
        self._exhaustion: Optional[dict[str, Any]] = None
        self._save_checkpoint: Optional[dict[str, Any]] = None
        self._mapping_evidence: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._candidate_token: Optional[tuple[str, Optional[int]]] = None
        self._candidate_count = 0
        self._armed_next_wave: Optional[int] = None
        self._pending: Optional[PerkCaptureRequest] = None
        self._last_completed_request: Optional[PerkCaptureRequest] = None

    @property
    def pending(self) -> Optional[PerkCaptureRequest]:
        return self._pending

    @property
    def pwr_maxed(self) -> bool:
        return self._pwr_maxed

    @property
    def latest_batch(self) -> Optional[dict[str, Any]]:
        return copy.deepcopy(self._batches[-1]) if self._batches else None

    @property
    def last_completed_request(self) -> Optional[PerkCaptureRequest]:
        return self._last_completed_request

    def observe(
        self,
        progress: PerkProgress,
        *,
        wave: Optional[int],
        boundary_observation_complete: bool = True,
        activity_scope_id: Optional[str] = None,
    ) -> Optional[PerkCaptureRequest]:
        """Observe progress and return a request after its token stabilizes."""

        token = progress.token
        if progress.status == "complete":
            try:
                complete_confidence = float(progress.confidence)
            except (TypeError, ValueError):
                token = None
            else:
                if (
                    not math.isfinite(complete_confidence)
                    or complete_confidence < DEFAULT_CONFIDENCE_THRESHOLD
                ):
                    token = None
        if token is None:
            self._candidate_token = None
            self._candidate_count = 0
            return self._pending
        if token == self._candidate_token:
            self._candidate_count += 1
        else:
            self._candidate_token = token
            self._candidate_count = 1
        if self._candidate_count < self._confirmation_frames:
            return self._pending
        if progress.status == "complete":
            self._record_exhaustion(
                progress,
                wave=wave,
                activity_scope_id=activity_scope_id,
            )
        if self._pending is not None:
            self._refresh_pending(
                progress,
                wave=wave,
                boundary_observation_complete=boundary_observation_complete,
            )
            return self._pending

        if not self._snapshot_known:
            self._pending = PerkCaptureRequest(
                kind="baseline",
                scheduled_wave=None,
                observed_wave=wave,
                progress_after=progress,
                snapshot_mode="full",
                observed_wave_end=wave,
            )
            return self._pending

        if self._armed_next_wave is None:
            if progress.status == "scheduled":
                self._armed_next_wave = progress.next_wave
            return None

        if (
            progress.status == "scheduled"
            and progress.current_wave is not None
            and self._armed_next_wave
            > progress.current_wave + MAX_PERK_SCHEDULE_LEAD_WAVES
        ):
            poisoned_wave = self._armed_next_wave
            self._armed_next_wave = progress.next_wave
            self._warn_once(
                "An implausible armed Perk wave "
                f"({poisoned_wave}) was discarded and the timeline "
                f"resynchronized at {progress.next_wave}"
            )
            return None

        transitioned = (
            progress.status == "complete"
            or (
                progress.current_wave is not None
                and progress.next_wave is not None
                and progress.current_wave >= self._armed_next_wave
                and progress.next_wave > self._armed_next_wave
            )
        )
        if not transitioned:
            return None

        self._pending = PerkCaptureRequest(
            kind="selection",
            scheduled_wave=self._armed_next_wave,
            observed_wave=wave,
            progress_after=progress,
            snapshot_mode=SELECTION_SCAN_MODE,
            scheduled_waves=(self._armed_next_wave,),
            observed_wave_end=wave,
            boundary_coverage=(
                BOUNDARY_COVERAGE_COMPLETE
                if boundary_observation_complete
                else BOUNDARY_COVERAGE_VISIBILITY_GAP
            ),
        )
        self._record_selection_boundary(self._pending, progress)
        return self._pending

    def confirmed_progress_resolves_visibility_gap(
        self,
        progress: PerkProgress,
    ) -> bool:
        """Return whether stable progress accounts for an unseen-screen gap."""

        token = progress.token
        if (
            token is None
            or token != self._candidate_token
            or self._candidate_count < self._confirmation_frames
        ):
            return False
        if self._pending is not None:
            return self._pending.progress_after.token == token
        if progress.status == "scheduled":
            return self._armed_next_wave == progress.next_wave
        return progress.status == "complete"

    def checkpoint(self) -> dict[str, Any]:
        """Return the internal state needed to continue the same battle."""

        pending = self._pending
        return {
            "schema_version": PERK_TIMELINE_CHECKPOINT_SCHEMA_VERSION,
            "fresh_battle": self._fresh_battle,
            "baseline_status": self._baseline_status,
            "snapshot_known": self._snapshot_known,
            "selected_by_family": copy.deepcopy(self._selected_by_family),
            "pwr_maxed": self._pwr_maxed,
            "batches": copy.deepcopy(self._batches),
            "selection_boundaries": copy.deepcopy(self._selection_boundaries),
            "exhaustion": copy.deepcopy(self._exhaustion),
            "save_checkpoint": copy.deepcopy(self._save_checkpoint),
            "warnings": list(self._warnings),
            "armed_next_wave": self._armed_next_wave,
            "pending": (
                _capture_request_checkpoint(pending)
                if pending is not None
                else None
            ),
        }

    def bind_exhaustion_identity(self, identity: Mapping[str, Any]) -> bool:
        """Promote persisted exhaustion after its exact save identity is known."""

        if self._exhaustion is None:
            return False
        try:
            normalized = _validated_active_round_identity(identity)
        except (TypeError, ValueError):
            return False
        current = self._exhaustion.get("active_round_identity")
        if current is not None and current != normalized:
            return False
        self._exhaustion["active_round_identity"] = normalized
        self._exhaustion["binding_status"] = "active_round_identity_bound"
        return True

    def restore_checkpoint(self, payload: Mapping[str, Any]) -> bool:
        """Restore one validated checkpoint without accepting partial state."""

        try:
            restored = _validated_tracker_checkpoint(payload)
        except (TypeError, ValueError):
            return False
        self._fresh_battle = restored["fresh_battle"]
        self._baseline_status = restored["baseline_status"]
        self._snapshot_known = restored["snapshot_known"]
        self._selected_by_family = restored["selected_by_family"]
        self._pwr_maxed = restored["pwr_maxed"]
        self._batches = restored["batches"]
        self._selection_boundaries = restored["selection_boundaries"]
        self._exhaustion = restored["exhaustion"]
        self._save_checkpoint = restored["save_checkpoint"]
        self._mapping_evidence = []
        self._warnings = restored["warnings"]
        self._armed_next_wave = restored["armed_next_wave"]
        self._pending = restored["pending"]
        self._last_completed_request = None
        self._candidate_token = None
        self._candidate_count = 0
        if self._save_checkpoint is not None:
            self._selected_by_family = _saved_selected_by_family(
                self._save_checkpoint["picks"]
            )
        return True

    def record_saved_checkpoint(self, checkpoint: Mapping[str, Any]) -> str:
        """Replace panel-derived state with one exact saved Perk prefix.

        Saved picks are historical positive evidence.  An identical checkpoint
        may advance provenance, while only a strict prefix extension may add
        timeline entries.  This method never interprets a missing later pick as
        proof that no later pick exists.
        """

        try:
            candidate = _validated_saved_perk_checkpoint(checkpoint)
        except (TypeError, ValueError):
            return "rejected_saved_checkpoint"

        previous = self._save_checkpoint
        previous_picks = previous["picks"] if previous is not None else []
        candidate_picks = candidate["picks"]
        if previous is None:
            new_picks = candidate_picks
            disposition = "initial_saved_prefix"
            # The exact saved sequence supersedes any same-process panel
            # baseline or batches.  Passive top-bar boundaries and exhaustion
            # remain useful independent evidence.
            self._batches = []
            self._mapping_evidence = []
            self._selected_by_family = {}
            self._snapshot_known = True
            self._baseline_status = (
                "save_backed_new_battle"
                if self._fresh_battle
                else "save_backed_mid_battle"
            )
        elif candidate_picks == previous_picks:
            if (
                candidate["saved_wave"] < previous["saved_wave"]
                or _aware_datetime(candidate["captured_at"])
                <= _aware_datetime(previous["captured_at"])
            ):
                return "ignored_lagging_saved_prefix"
            new_picks = []
            disposition = "unchanged_saved_prefix_observed_later"
        elif (
            len(candidate_picks) > len(previous_picks)
            and candidate_picks[: len(previous_picks)] == previous_picks
        ):
            if (
                candidate["saved_wave"] < previous["saved_wave"]
                or _aware_datetime(candidate["captured_at"])
                <= _aware_datetime(previous["captured_at"])
            ):
                return "rejected_saved_prefix_freshness"
            new_picks = candidate_picks[len(previous_picks) :]
            disposition = "strict_saved_prefix_extension"
        else:
            return "rejected_saved_prefix_conflict"

        for pick in new_picks:
            self._append_saved_pick(pick, checkpoint=candidate)
        self._save_checkpoint = candidate
        self._selected_by_family = _saved_selected_by_family(candidate_picks)
        self._snapshot_known = True
        pwr_level = next(
            (
                int(level["level"])
                for level in candidate["levels"]
                if level["perk_key"] == PWR_FAMILY
            ),
            0,
        )
        if previous is None:
            self._pwr_maxed = pwr_level >= 3
        elif pwr_level >= 3:
            self._pwr_maxed = True

        request = self._pending
        if request is not None and (
            request.kind == "baseline" or bool(new_picks)
        ):
            self._advance_after_capture(request)
        return disposition

    def record_full_snapshot(
        self,
        capture: Mapping[str, Any],
        *,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Accept a complete selected-list capture for a baseline or batch."""

        request = self._pending
        if request is None or request.snapshot_mode not in {
            "full",
            SELECTION_SCAN_MODE,
        }:
            return False
        quality = capture.get("quality")
        selected = capture.get("selected")
        if (
            not isinstance(quality, Mapping)
            or not quality.get("source_complete")
            or not isinstance(selected, Sequence)
            or isinstance(selected, (str, bytes))
        ):
            self._warn_once(
                "A complete Perks panel snapshot could not be read; "
                "the pending timeline event will be retried"
            )
            return False

        after = _index_selected_perks(selected)
        if request.kind == "baseline":
            self._selected_by_family = after
            self._snapshot_known = True
            self._baseline_status = "observed_mid_battle"
            self._pwr_maxed = _snapshot_has_max_pwr(after)
            self._advance_after_capture(request)
            return True

        changes = _diff_selected_perks(self._selected_by_family, after)
        if not changes:
            self._warn_once(
                "The Perks panel did not yet show the scheduled selection; "
                "the pending timeline event will be retried"
            )
            return False
        request = self._record_observed_changes(
            request,
            changes,
            observed_at=observed_at,
        )
        self._selected_by_family = after
        if _snapshot_has_max_pwr(after):
            self._pwr_maxed = True
        self._advance_after_capture(request)
        return True

    def selection_is_unchanged(self, perk: Mapping[str, Any]) -> bool:
        """Return whether one visible row matches the persisted family state."""

        try:
            confidence = float(perk.get("confidence"))
        except (TypeError, ValueError):
            return False
        if confidence < DEFAULT_CONFIDENCE_THRESHOLD:
            return False
        normalized = _timeline_entry(perk)
        if normalized is None:
            return False
        previous = self._selected_by_family.get(normalized["family"])
        return bool(previous and _same_display(previous, normalized))

    def record_snapshot_to_unchanged(
        self,
        capture: Mapping[str, Any],
        *,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Accept a newest-first prefix ending at the first unchanged row."""

        request = self._pending
        if (
            request is None
            or request.snapshot_mode != SELECTION_SCAN_MODE
            or not self._selected_by_family
        ):
            return False
        quality = capture.get("quality")
        selected = capture.get("selected")
        if (
            not isinstance(quality, Mapping)
            or not quality.get("source_complete")
            or not isinstance(selected, Sequence)
            or isinstance(selected, (str, bytes))
        ):
            return False
        normalized = [
            entry
            for raw in selected
            if isinstance(raw, Mapping)
            for entry in [_timeline_entry(raw)]
            if entry is not None
        ]
        unchanged_index = next(
            (
                index
                for index, entry in enumerate(normalized)
                if self.selection_is_unchanged(entry)
            ),
            None,
        )
        if unchanged_index is None:
            return False
        changed_prefix = normalized[:unchanged_index]
        if not changed_prefix:
            self._warn_once(
                "The Perks panel did not yet show a selection newer than the "
                "persisted timeline marker; the pending event will be retried"
            )
            return False

        after = copy.deepcopy(self._selected_by_family)
        changes = []
        for entry in changed_prefix:
            previous = self._selected_by_family.get(entry["family"])
            if previous is None or not _same_display(previous, entry):
                changes.append(_selection_change(previous, entry))
            after[entry["family"]] = entry
        if not changes:
            self._warn_once(
                "The Perks catch-up rows did not change the persisted snapshot; "
                "the pending timeline event will be retried"
            )
            return False
        request = self._record_observed_changes(
            request,
            changes,
            observed_at=observed_at,
        )
        self._selected_by_family = after
        if _snapshot_has_max_pwr(after):
            self._pwr_maxed = True
        self._advance_after_capture(request)
        return True

    def _record_observed_changes(
        self,
        request: PerkCaptureRequest,
        changes: Sequence[Mapping[str, Any]],
        *,
        observed_at: Optional[datetime],
    ) -> PerkCaptureRequest:
        """Record a complete changed prefix with honest boundary semantics."""

        if (
            self._pwr_maxed
            and request.boundary_coverage == BOUNDARY_COVERAGE_COMPLETE
            and len(changes) > len(request.scheduled_waves)
        ):
            request = replace(
                request,
                boundary_coverage=BOUNDARY_COVERAGE_VISIBILITY_GAP,
            )
            self._pending = request
        if (
            self._pwr_maxed
            and len(request.scheduled_waves) > 1
            and len(changes) == len(request.scheduled_waves)
            and request.boundary_coverage == BOUNDARY_COVERAGE_COMPLETE
        ):
            self._append_ordered_post_pwr_batches(
                request,
                changes,
                observed_at=observed_at,
            )
            return request

        self._append_batch(request, changes, observed_at=observed_at)
        if request.boundary_coverage != BOUNDARY_COVERAGE_COMPLETE:
            self._warn_once(
                "Perk panel capture followed an interval when the top-bar "
                "schedule was not observable; net changes are recorded as an "
                "interval aggregate without per-wave attribution"
            )
        elif len(request.scheduled_waves) > 1:
            detail = (
                "because the number of distinct changes did not match the "
                "number of scheduled boundaries"
                if self._pwr_maxed
                else "because the interval includes pre-max PWR cascades"
            )
            self._warn_once(
                "Perk panel capture was deferred across multiple selection "
                "boundaries; changes are recorded as an interval aggregate "
                f"without per-wave attribution {detail}"
            )
        return request

    def snapshot(self) -> dict[str, Any]:
        """Return a detached battle-record payload."""

        return {
            "schema_version": 4,
            "source": (
                "player_save_perk_prefix_with_passive_top_bar"
                if self._save_checkpoint is not None
                else "passive_top_bar_awaiting_player_save"
            ),
            "batch_order_semantics": "selection_wave_order",
            "within_batch_order_semantics": "simultaneous_unordered",
            "deferred_post_pwr_order_semantics": (
                "latest_selected_first_reconstructed_when_one_distinct_"
                "change_matches_each_scheduled_boundary"
            ),
            "selection_scan_semantics": (
                "newest_first_until_first_unchanged_row_with_full_edge_"
                "fallback"
            ),
            "baseline_status": self._baseline_status,
            "pwr_maxed_observed": self._pwr_maxed,
            "batches": copy.deepcopy(self._batches),
            "save_backed_prefix": (
                _saved_checkpoint_provenance(self._save_checkpoint)
                if self._save_checkpoint is not None
                else None
            ),
            "passive_top_bar": {
                "selection_boundaries": copy.deepcopy(
                    self._selection_boundaries
                ),
                "exhaustion": copy.deepcopy(self._exhaustion),
            },
            "warnings": list(self._warnings),
            "pending_scheduled_wave": (
                self._pending.scheduled_wave if self._pending else None
            ),
            "pending_scheduled_waves": (
                list(self._pending.scheduled_waves)
                if self._pending is not None
                else []
            ),
            "pending_boundary_coverage": (
                self._pending.boundary_coverage
                if self._pending is not None
                else None
            ),
        }

    def current_perks_presentation(self) -> dict[str, Any]:
        """Return the save-backed current inventory for read-only clients.

        The persisted tracker checkpoint remains the authority.  This compact
        additive projection deliberately omits the private round identity and
        save payload details while preserving the checkpoint's honest
        freshness boundary.
        """

        checkpoint = self._save_checkpoint
        common: dict[str, Any] = {
            "schema_version": CURRENT_PERKS_PRESENTATION_SCHEMA_VERSION,
            "source": "monitor_validated_player_save_perk_prefix",
            "order_semantics": "most_recent_selection_first",
        }
        if checkpoint is None:
            return {
                **common,
                "status": "awaiting_save_checkpoint",
                "reason": "save_checkpoint_unavailable",
                "captured_at": None,
                "saved_wave": None,
                "picked_count": 0,
                "unique_count": 0,
                "items": [],
            }

        latest_by_key: dict[str, dict[str, Any]] = {}
        for pick in checkpoint["picks"]:
            perk_key = str(pick["perk_key"])
            latest_by_key[perk_key] = {
                "perk_key": perk_key,
                "label": perk_configuration_label(perk_key),
                "level": int(pick["level_after"]),
                "last_selected_wave": int(pick["saved_wave"]),
                "last_selected_sequence": int(pick["sequence"]),
            }
        items = sorted(
            latest_by_key.values(),
            key=lambda item: item["last_selected_sequence"],
            reverse=True,
        )
        return {
            **common,
            "status": "available",
            "reason": "",
            "captured_at": str(checkpoint["captured_at"]),
            "saved_wave": int(checkpoint["saved_wave"]),
            "picked_count": int(checkpoint["picked_count"]),
            "unique_count": len(items),
            "items": items,
        }

    def _append_saved_pick(
        self,
        pick: Mapping[str, Any],
        *,
        checkpoint: Mapping[str, Any],
    ) -> None:
        """Append one exact oldest-first save pick without UI calibration."""

        level_after = int(pick["level_after"])
        label = perk_configuration_label(str(pick["perk_key"]))
        display = f"{label} (level {level_after})"
        selection = {
            "family": str(pick["perk_key"]),
            "perk_key": str(pick["perk_key"]),
            "perk_id": int(pick["perk_id"]),
            "display_text": display,
            "color": "save_backed",
            "instance_model": "save_backed_level",
            "confidence": 100.0,
            "change": "added" if level_after == 1 else "level_changed",
            "level_after": level_after,
            "saved_sequence": int(pick["sequence"]),
            "source": "exact_saved_pick",
        }
        if level_after > 1:
            selection["before_display_text"] = (
                f"{label} (level {level_after - 1})"
            )
        self._batches.append(
            {
                "sequence": len(self._batches) + 1,
                "scheduled_wave": int(pick["saved_wave"]),
                "scheduled_waves": [int(pick["saved_wave"])],
                "observed_wave": int(checkpoint["saved_wave"]),
                "observed_wave_end": int(checkpoint["saved_wave"]),
                "boundary_coverage": BOUNDARY_COVERAGE_COMPLETE,
                "observed_at": str(checkpoint["captured_at"]),
                "selection_model": "exact_saved_pick",
                "snapshot_mode": "player_save_checkpoint",
                "selections": [selection],
            }
        )

    def drain_mapping_evidence(self) -> tuple[dict[str, Any], ...]:
        """Return new privacy-safe calibration batches exactly once.

        Restored timeline checkpoints never repopulate this process-local
        buffer, so a new save-audit session cannot inherit UI calibration
        evidence from an earlier process.
        """

        evidence = tuple(copy.deepcopy(self._mapping_evidence))
        self._mapping_evidence.clear()
        return evidence

    def _append_batch(
        self,
        request: PerkCaptureRequest,
        changes: Sequence[Mapping[str, Any]],
        *,
        observed_at: Optional[datetime],
        selection_model: Optional[str] = None,
    ) -> None:
        when = observed_at or datetime.now().astimezone()
        interval_aggregate = bool(
            len(request.scheduled_waves) > 1
            or request.boundary_coverage != BOUNDARY_COVERAGE_COMPLETE
        )
        batch = {
            "sequence": len(self._batches) + 1,
            "scheduled_wave": request.scheduled_wave,
            "scheduled_waves": list(request.scheduled_waves),
            "observed_wave": request.observed_wave,
            "observed_wave_end": request.observed_wave_end,
            "boundary_coverage": request.boundary_coverage,
            "observed_at": when.isoformat(),
            "selection_model": (
                selection_model
                or (
                    "interval_aggregate"
                    if interval_aggregate
                    else (
                        "singleton_after_pwr_max"
                        if self._pwr_maxed and len(changes) == 1
                        else "simultaneous_batch"
                    )
                )
            ),
            "snapshot_mode": request.snapshot_mode,
            "selections": [copy.deepcopy(dict(change)) for change in changes],
        }
        self._batches.append(batch)
        self._mapping_evidence.append(_mapping_evidence_from_batch(batch))

    def _append_ordered_post_pwr_batches(
        self,
        request: PerkCaptureRequest,
        changes_newest_first: Sequence[Mapping[str, Any]],
        *,
        observed_at: Optional[datetime],
    ) -> None:
        """Reconstruct deferred post-PWR singletons from newest-first rows."""

        source_waves = list(request.scheduled_waves)
        for scheduled_wave, change in zip(
            source_waves,
            reversed(changes_newest_first),
        ):
            reconstructed = replace(
                request,
                scheduled_wave=scheduled_wave,
                scheduled_waves=(scheduled_wave,),
            )
            self._append_batch(
                reconstructed,
                [change],
                observed_at=observed_at,
                selection_model="singleton_after_pwr_max_reconstructed",
            )
            self._batches[-1]["reconstructed_from_scheduled_waves"] = (
                source_waves
            )
            self._batches[-1]["source_order_semantics"] = (
                "latest_selected_first"
            )

    def _advance_after_capture(self, request: PerkCaptureRequest) -> None:
        if request.progress_after.status == "scheduled":
            self._armed_next_wave = request.progress_after.next_wave
        else:
            self._armed_next_wave = None
        self._last_completed_request = request
        self._pending = None

    def _refresh_pending(
        self,
        progress: PerkProgress,
        *,
        wave: Optional[int],
        boundary_observation_complete: bool,
    ) -> None:
        """Advance deferred capture authority to the newest stable token."""

        request = self._pending
        if request is None or progress.token == request.progress_after.token:
            return
        previous_next = request.progress_after.next_wave
        advanced = (
            progress.status == "complete"
            or (
                progress.current_wave is not None
                and progress.next_wave is not None
                and previous_next is not None
                and progress.current_wave >= previous_next
                and progress.next_wave > previous_next
            )
        )
        if not advanced:
            return

        if request.kind == "baseline":
            self._pending = replace(
                request,
                progress_after=progress,
                observed_wave_end=wave,
            )
            return

        scheduled_waves = list(request.scheduled_waves)
        if previous_next is not None and previous_next not in scheduled_waves:
            scheduled_waves.append(previous_next)
        boundary_coverage = (
            request.boundary_coverage
            if boundary_observation_complete
            else BOUNDARY_COVERAGE_VISIBILITY_GAP
        )
        self._pending = replace(
            request,
            progress_after=progress,
            scheduled_waves=tuple(scheduled_waves),
            observed_wave_end=wave,
            boundary_coverage=boundary_coverage,
        )
        if previous_next is not None:
            self._record_selection_boundary(self._pending, progress)

    def _record_selection_boundary(
        self,
        request: PerkCaptureRequest,
        progress: PerkProgress,
    ) -> None:
        scheduled_wave = request.scheduled_waves[-1]
        if any(
            item.get("scheduled_wave") == scheduled_wave
            for item in self._selection_boundaries
        ):
            return
        self._selection_boundaries.append(
            {
                "scheduled_wave": scheduled_wave,
                "observed_wave": request.observed_wave_end,
                "observed_at": progress.observed_at,
                "boundary_coverage": request.boundary_coverage,
                "source": "stable_top_bar_schedule_transition",
            }
        )

    def _record_exhaustion(
        self,
        progress: PerkProgress,
        *,
        wave: Optional[int],
        activity_scope_id: Optional[str],
    ) -> None:
        scope_id = str(activity_scope_id or "").strip()
        if (
            type(wave) is not int
            or wave < 0
            or not scope_id
            or not isinstance(progress.observed_at, str)
            or not progress.observed_at
            or not isinstance(progress.source_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", progress.source_fingerprint) is None
            or float(progress.confidence) < DEFAULT_CONFIDENCE_THRESHOLD
        ):
            return
        current = self._exhaustion
        if current is not None:
            current["stable_observation_count"] = max(
                int(current.get("stable_observation_count") or 0),
                self._candidate_count,
            )
            current["ocr_confidence"] = max(
                float(current.get("ocr_confidence") or 0.0),
                float(progress.confidence),
            )
            return
        event_material = (
            f"{scope_id}|{wave}|{progress.observed_at}|"
            f"{progress.source_fingerprint}"
        )
        self._exhaustion = {
            "schema_version": 1,
            "source": "stable_top_bar_view_perks",
            "event_id": hashlib.sha256(event_material.encode("utf-8")).hexdigest(),
            "activity_scope_id": scope_id,
            "binding_status": "pending_active_round_identity",
            "observed_wave": wave,
            "observed_at": progress.observed_at,
            "stable_observation_count": self._candidate_count,
            "ocr_confidence": float(progress.confidence),
            "capture_provenance": {
                "source": "main_loop_frame",
                "region": progress.source_region,
                "source_fingerprint": progress.source_fingerprint,
            },
        }

    def _warn_once(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)


def _capture_request_checkpoint(request: PerkCaptureRequest) -> dict[str, Any]:
    progress = request.progress_after
    return {
        "kind": request.kind,
        "scheduled_wave": request.scheduled_wave,
        "observed_wave": request.observed_wave,
        "progress_after": {
            "status": progress.status,
            "current_wave": progress.current_wave,
            "next_wave": progress.next_wave,
            "text_raw": progress.text_raw,
            "confidence": progress.confidence,
            "observed_at": progress.observed_at,
            "source_fingerprint": progress.source_fingerprint,
            "source_region": progress.source_region,
        },
        "snapshot_mode": request.snapshot_mode,
        "scheduled_waves": list(request.scheduled_waves),
        "observed_wave_end": request.observed_wave_end,
        "boundary_coverage": request.boundary_coverage,
    }


def _validated_saved_perk_checkpoint(payload: Any) -> dict[str, Any]:
    """Normalize the exact monitor checkpoint allowed into timeline state."""

    if not isinstance(payload, Mapping):
        raise TypeError("saved Perk checkpoint must be a mapping")
    if payload.get("schema_version") != 1 or payload.get("complete") is not True:
        raise ValueError("saved Perk checkpoint is incomplete")
    mapping_id = str(payload.get("mapping_id") or "").strip()
    audit_matrix_id = str(payload.get("audit_matrix_id") or "").strip()
    game_version = payload.get("game_version")
    save_revision = payload.get("save_revision")
    saved_wave = payload.get("saved_wave")
    picked_count = payload.get("picked_count")
    prefix_fingerprint = str(payload.get("prefix_fingerprint") or "")
    captured_at = str(payload.get("captured_at") or "")
    if (
        not mapping_id
        or not audit_matrix_id
        or type(game_version) is not int
        or game_version < 0
        or type(save_revision) is not int
        or save_revision < 0
        or type(saved_wave) is not int
        or saved_wave < 0
        or type(picked_count) is not int
        or picked_count < 0
        or re.fullmatch(r"[0-9a-f]{64}", prefix_fingerprint) is None
        or not _valid_aware_timestamp(captured_at)
    ):
        raise ValueError("saved Perk checkpoint provenance is invalid")
    identity = _validated_active_round_identity(
        payload.get("active_round_identity")
    )
    if identity["game_version"] != game_version:
        raise ValueError("saved Perk checkpoint identity version changed")

    raw_picks = payload.get("picks")
    if (
        not isinstance(raw_picks, Sequence)
        or isinstance(raw_picks, (str, bytes, bytearray))
        or len(raw_picks) != picked_count
    ):
        raise ValueError("saved Perk pick prefix is invalid")
    picks: list[dict[str, Any]] = []
    levels_by_id: dict[int, tuple[str, int]] = {}
    ids_by_key: dict[str, int] = {}
    prior_wave = -1
    for sequence, raw in enumerate(raw_picks, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("saved Perk pick is malformed")
        pick_wave = raw.get("saved_wave")
        perk_id = raw.get("perk_id")
        perk_key = str(raw.get("perk_key") or "")
        level_after = raw.get("level_after")
        if (
            raw.get("sequence") != sequence
            or type(pick_wave) is not int
            or pick_wave < prior_wave
            or pick_wave > saved_wave
            or type(perk_id) is not int
            or perk_id < 0
            or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", perk_key) is None
            or type(level_after) is not int
            or level_after < 1
        ):
            raise ValueError("saved Perk pick is malformed")
        previous = levels_by_id.get(perk_id)
        if previous is not None and previous[0] != perk_key:
            raise ValueError("saved Perk ID changed meaning")
        previous_id = ids_by_key.get(perk_key)
        if previous_id is not None and previous_id != perk_id:
            raise ValueError("saved Perk key changed ID")
        if level_after != (previous[1] if previous is not None else 0) + 1:
            raise ValueError("saved Perk level is not monotonic")
        levels_by_id[perk_id] = (perk_key, level_after)
        ids_by_key[perk_key] = perk_id
        prior_wave = pick_wave
        picks.append(
            {
                "sequence": sequence,
                "saved_wave": pick_wave,
                "perk_id": perk_id,
                "perk_key": perk_key,
                "level_after": level_after,
                "source": "exact_saved_pick",
            }
        )

    raw_levels = payload.get("levels")
    expected_levels = [
        {"perk_id": perk_id, "perk_key": key, "level": level}
        for perk_id, (key, level) in sorted(levels_by_id.items())
    ]
    normalized_levels = []
    if isinstance(raw_levels, Sequence) and not isinstance(
        raw_levels, (str, bytes, bytearray)
    ):
        normalized_levels = [
            {
                "perk_id": raw.get("perk_id"),
                "perk_key": raw.get("perk_key"),
                "level": raw.get("level"),
            }
            for raw in raw_levels
            if isinstance(raw, Mapping)
        ]
    if normalized_levels != expected_levels:
        raise ValueError("saved Perk levels disagree with the pick prefix")

    return {
        "schema_version": 1,
        "mapping_id": mapping_id,
        "audit_matrix_id": audit_matrix_id,
        "game_version": game_version,
        "save_revision": save_revision,
        "saved_wave": saved_wave,
        "captured_at": captured_at,
        "active_round_identity": identity,
        "complete": True,
        "picked_count": picked_count,
        "order_semantics": "oldest_selected_first_exact_saved_order",
        "picks": picks,
        "levels": expected_levels,
        "prefix_fingerprint": prefix_fingerprint,
        "acceptance": str(payload.get("acceptance") or "accepted"),
        "acquisition_type": str(payload.get("acquisition_type") or "unknown"),
    }


def _saved_selected_by_family(
    picks: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for pick in picks:
        perk_key = str(pick["perk_key"])
        level_after = int(pick["level_after"])
        selected[perk_key] = {
            "family": perk_key,
            "display_text": (
                f"{perk_configuration_label(perk_key)} (level {level_after})"
            ),
            "color": "save_backed",
            "instance_model": "save_backed_level",
            "confidence": 100.0,
            "perk_id": int(pick["perk_id"]),
            "level_after": level_after,
            "source": "exact_saved_pick",
        }
    return selected


def _saved_checkpoint_provenance(
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "mapping_id": checkpoint.get("mapping_id"),
        "game_version": checkpoint.get("game_version"),
        "save_revision": checkpoint.get("save_revision"),
        "saved_wave": checkpoint.get("saved_wave"),
        "captured_at": checkpoint.get("captured_at"),
        "picked_count": checkpoint.get("picked_count"),
        "prefix_fingerprint": checkpoint.get("prefix_fingerprint"),
        "active_round_identity": copy.deepcopy(
            checkpoint.get("active_round_identity")
        ),
    }


def _validated_tracker_checkpoint(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != PERK_TIMELINE_CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported tracker checkpoint schema")
    fresh_battle = _required_bool(payload.get("fresh_battle"))
    snapshot_known = _required_bool(payload.get("snapshot_known"))
    pwr_maxed = _required_bool(payload.get("pwr_maxed"))
    baseline_status = str(payload.get("baseline_status") or "")
    if baseline_status not in {
        "new_battle_empty",
        "not_observed",
        "observed_mid_battle",
        "save_backed_new_battle",
        "save_backed_mid_battle",
    }:
        raise ValueError("invalid tracker baseline status")
    if baseline_status == "new_battle_empty" and not snapshot_known:
        raise ValueError("fresh-battle checkpoint lacks a known snapshot")
    if baseline_status == "not_observed" and snapshot_known:
        raise ValueError("unknown baseline cannot have a known snapshot")

    raw_save_checkpoint = payload.get("save_checkpoint")
    save_checkpoint = (
        _validated_saved_perk_checkpoint(raw_save_checkpoint)
        if raw_save_checkpoint is not None
        else None
    )
    if baseline_status.startswith("save_backed_") and save_checkpoint is None:
        raise ValueError("save-backed baseline lacks its exact checkpoint")
    if save_checkpoint is not None and not snapshot_known:
        raise ValueError("save-backed checkpoint lacks a known snapshot")

    selected_raw = payload.get("selected_by_family")
    if not isinstance(selected_raw, Mapping):
        raise ValueError("selected Perks checkpoint must be a mapping")
    if save_checkpoint is not None:
        selected_by_family = _saved_selected_by_family(
            save_checkpoint["picks"]
        )
    else:
        selected_by_family = {}
        for raw_family, raw_entry in selected_raw.items():
            family = str(raw_family or "").strip()
            if not family or not isinstance(raw_entry, Mapping):
                raise ValueError("invalid selected Perk checkpoint entry")
            entry = _timeline_entry(raw_entry)
            if entry is None or entry["family"] != family:
                raise ValueError("selected Perk checkpoint family mismatch")
            selected_by_family[family] = entry

    raw_batches = payload.get("batches")
    if (
        not isinstance(raw_batches, Sequence)
        or isinstance(raw_batches, (str, bytes))
        or not all(isinstance(batch, Mapping) for batch in raw_batches)
    ):
        raise ValueError("invalid tracker batch checkpoint")
    batches = [copy.deepcopy(dict(batch)) for batch in raw_batches]

    raw_boundaries = payload.get("selection_boundaries")
    if (
        not isinstance(raw_boundaries, Sequence)
        or isinstance(raw_boundaries, (str, bytes))
        or not all(isinstance(item, Mapping) for item in raw_boundaries)
    ):
        raise ValueError("invalid passive selection-boundary checkpoint")
    selection_boundaries: list[dict[str, Any]] = []
    seen_boundary_waves: set[int] = set()
    for raw_boundary in raw_boundaries:
        scheduled_wave = _required_positive_int(
            raw_boundary.get("scheduled_wave")
        )
        coverage = str(raw_boundary.get("boundary_coverage") or "")
        if (
            scheduled_wave in seen_boundary_waves
            or coverage
            not in {
                BOUNDARY_COVERAGE_COMPLETE,
                BOUNDARY_COVERAGE_VISIBILITY_GAP,
            }
            or raw_boundary.get("source")
            != "stable_top_bar_schedule_transition"
        ):
            raise ValueError("invalid passive selection-boundary checkpoint")
        seen_boundary_waves.add(scheduled_wave)
        selection_boundaries.append(copy.deepcopy(dict(raw_boundary)))

    raw_exhaustion = payload.get("exhaustion")
    exhaustion = None
    if raw_exhaustion is not None:
        if not isinstance(raw_exhaustion, Mapping):
            raise ValueError("invalid exhaustion checkpoint")
        event_id = str(raw_exhaustion.get("event_id") or "")
        provenance = raw_exhaustion.get("capture_provenance")
        binding_status = str(raw_exhaustion.get("binding_status") or "")
        raw_identity = raw_exhaustion.get("active_round_identity")
        if binding_status == "active_round_identity_bound":
            try:
                _validated_active_round_identity(raw_identity)
            except (TypeError, ValueError):
                raise ValueError("invalid exhaustion checkpoint") from None
        elif (
            binding_status != "pending_active_round_identity"
            or raw_identity is not None
        ):
            raise ValueError("invalid exhaustion checkpoint")
        confidence = raw_exhaustion.get("ocr_confidence")
        if (
            raw_exhaustion.get("schema_version") != 1
            or raw_exhaustion.get("source") != "stable_top_bar_view_perks"
            or re.fullmatch(r"[0-9a-f]{64}", event_id) is None
            or not str(raw_exhaustion.get("activity_scope_id") or "")
            or type(raw_exhaustion.get("observed_wave")) is not int
            or raw_exhaustion["observed_wave"] < 0
            or type(raw_exhaustion.get("stable_observation_count")) is not int
            or raw_exhaustion["stable_observation_count"] < 2
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not DEFAULT_CONFIDENCE_THRESHOLD <= float(confidence) <= 100
            or not _valid_aware_timestamp(raw_exhaustion.get("observed_at"))
            or not isinstance(provenance, Mapping)
            or provenance.get("source") != "main_loop_frame"
            or provenance.get("region") != "perk_progress_text"
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(provenance.get("source_fingerprint") or ""),
            )
            is None
        ):
            raise ValueError("invalid exhaustion checkpoint")
        exhaustion = copy.deepcopy(dict(raw_exhaustion))

    raw_warnings = payload.get("warnings")
    if (
        not isinstance(raw_warnings, Sequence)
        or isinstance(raw_warnings, (str, bytes))
        or not all(isinstance(warning, str) for warning in raw_warnings)
    ):
        raise ValueError("invalid tracker warning checkpoint")
    warnings = [str(warning) for warning in raw_warnings]
    armed_next_wave = _optional_positive_int(payload.get("armed_next_wave"))
    pending = _validated_capture_request_checkpoint(payload.get("pending"))
    return {
        "fresh_battle": fresh_battle,
        "baseline_status": baseline_status,
        "snapshot_known": snapshot_known,
        "selected_by_family": selected_by_family,
        "pwr_maxed": pwr_maxed,
        "batches": batches,
        "selection_boundaries": selection_boundaries,
        "exhaustion": exhaustion,
        "save_checkpoint": save_checkpoint,
        "warnings": warnings,
        "armed_next_wave": armed_next_wave,
        "pending": pending,
    }


def _validated_active_round_identity(identity: Any) -> dict[str, Any]:
    if not isinstance(identity, Mapping):
        raise TypeError("active-round identity must be a mapping")
    normalized = {
        "game_version": identity.get("game_version"),
        "current_tier": identity.get("current_tier"),
        "rounds_started_this_tier": identity.get("rounds_started_this_tier"),
        "round_seed": identity.get("round_seed"),
        "fingerprint": identity.get("fingerprint"),
    }
    if (
        type(normalized["game_version"]) is not int
        or normalized["game_version"] < 0
        or type(normalized["current_tier"]) is not int
        or normalized["current_tier"] < 0
        or type(normalized["rounds_started_this_tier"]) is not int
        or normalized["rounds_started_this_tier"] < 0
        or type(normalized["round_seed"]) is not int
        or normalized["round_seed"] <= 0
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(normalized["fingerprint"] or ""),
        )
        is None
    ):
        raise ValueError("active-round identity is invalid")
    return normalized


def _valid_aware_timestamp(value: Any) -> bool:
    try:
        _aware_datetime(value)
    except (TypeError, ValueError):
        return False
    return True


def _aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _validated_capture_request_checkpoint(
    payload: Any,
) -> Optional[PerkCaptureRequest]:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("invalid pending Perk checkpoint")
    kind = str(payload.get("kind") or "")
    snapshot_mode = str(payload.get("snapshot_mode") or "")
    boundary_coverage = str(
        payload.get("boundary_coverage") or BOUNDARY_COVERAGE_COMPLETE
    )
    if kind not in {"baseline", "selection"}:
        raise ValueError("invalid pending Perk request kind")
    if snapshot_mode not in {"full", SELECTION_SCAN_MODE}:
        raise ValueError("invalid pending Perk snapshot mode")
    if boundary_coverage not in {
        BOUNDARY_COVERAGE_COMPLETE,
        BOUNDARY_COVERAGE_VISIBILITY_GAP,
    }:
        raise ValueError("invalid pending Perk boundary coverage")
    progress_raw = payload.get("progress_after")
    if not isinstance(progress_raw, Mapping):
        raise ValueError("pending Perk progress is missing")
    status = str(progress_raw.get("status") or "")
    current_wave = _optional_positive_int(progress_raw.get("current_wave"))
    next_wave = _optional_positive_int(progress_raw.get("next_wave"))
    if status not in {"scheduled", "complete"}:
        raise ValueError("pending Perk progress is not authoritative")
    progress = PerkProgress(
        status=status,
        current_wave=current_wave,
        next_wave=next_wave,
        text_raw=str(progress_raw.get("text_raw") or ""),
        confidence=float(progress_raw.get("confidence")),
        observed_at=(
            str(progress_raw.get("observed_at"))
            if progress_raw.get("observed_at") is not None
            else None
        ),
        source_fingerprint=(
            str(progress_raw.get("source_fingerprint"))
            if progress_raw.get("source_fingerprint") is not None
            else None
        ),
        source_region=str(
            progress_raw.get("source_region") or "perk_progress_text"
        ),
    )
    if progress.token is None:
        raise ValueError("pending Perk progress is implausible")

    scheduled_wave = _optional_positive_int(payload.get("scheduled_wave"))
    observed_wave = _optional_positive_int(payload.get("observed_wave"))
    observed_wave_end = _optional_positive_int(payload.get("observed_wave_end"))
    raw_scheduled_waves = payload.get("scheduled_waves")
    if (
        not isinstance(raw_scheduled_waves, Sequence)
        or isinstance(raw_scheduled_waves, (str, bytes))
    ):
        raise ValueError("invalid pending scheduled waves")
    scheduled_waves = tuple(
        _required_positive_int(value) for value in raw_scheduled_waves
    )
    if kind == "baseline":
        if snapshot_mode != "full":
            raise ValueError("baseline checkpoint must use a full snapshot")
        if scheduled_wave is not None or scheduled_waves:
            raise ValueError("baseline checkpoint has scheduled selections")
    else:
        if snapshot_mode != SELECTION_SCAN_MODE:
            raise ValueError("selection checkpoint must use bounded scanning")
        if scheduled_wave is None or not scheduled_waves:
            raise ValueError("selection checkpoint lacks a scheduled wave")
        if scheduled_waves[0] != scheduled_wave:
            raise ValueError(
                "selection checkpoint has inconsistent scheduled waves"
            )

    return PerkCaptureRequest(
        kind=kind,
        scheduled_wave=scheduled_wave,
        observed_wave=observed_wave,
        progress_after=progress,
        snapshot_mode=snapshot_mode,
        scheduled_waves=scheduled_waves,
        observed_wave_end=observed_wave_end,
        boundary_coverage=boundary_coverage,
    )


def _required_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("checkpoint boolean is invalid")
    return value


def _required_positive_int(value: Any) -> int:
    parsed = _optional_positive_int(value)
    if parsed is None:
        raise ValueError("checkpoint wave must be positive")
    return parsed


def _optional_positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("checkpoint wave cannot be boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("checkpoint wave is invalid") from None
    if parsed <= 0:
        raise ValueError("checkpoint wave must be positive")
    return parsed


def _current_activity_scope_id() -> Optional[str]:
    scope = get_activity_scope()
    if not isinstance(scope, Mapping):
        return None
    run_id = str(scope.get("run_id") or "").strip()
    return run_id or None


def _load_perk_timeline_checkpoint(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("checkpoint root must be an object")
    return payload


def _write_perk_timeline_checkpoint(
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


class PerkTimelineObserver:
    """Own the bounded Perks-panel route needed by a timeline tracker."""

    def __init__(
        self,
        tracker: Optional[PerkTimelineTracker] = None,
        *,
        state_path: Optional[str | Path] = None,
        scope_id_fn: Callable[[], Optional[str]] = (
            lambda: _current_activity_scope_id()
        ),
    ) -> None:
        self.tracker = tracker or PerkTimelineTracker()
        self._route_open = False
        self._invalid_progress_count = 0
        self._invalid_progress_warned = False
        self._progress_visibility_interrupted = False
        self._state_path = Path(state_path) if state_path is not None else None
        self._scope_id_fn = scope_id_fn
        self._active_scope_id: Optional[str] = None
        self._last_persisted_payload: Optional[dict[str, Any]] = None
        self._persistence_warning_active = False
        if self._state_path is not None:
            self._sync_persistence_scope(restore=True)

    def reset(self, *, fresh_battle: bool = True) -> None:
        self._activate_current_scope()
        self.tracker.reset(fresh_battle=fresh_battle)
        self._route_open = False
        self._invalid_progress_count = 0
        self._invalid_progress_warned = False
        self._progress_visibility_interrupted = False
        self._persist_state()

    def snapshot(self) -> dict[str, Any]:
        self._sync_persistence_scope(restore=True)
        return self.tracker.snapshot()

    def drain_mapping_evidence(self) -> tuple[dict[str, Any], ...]:
        """Drain only calibration batches accepted by this live process."""

        self._sync_persistence_scope(restore=True)
        return self.tracker.drain_mapping_evidence()

    def observe_saved_checkpoint(self, checkpoint: Mapping[str, Any]) -> str:
        """Persist one monitor-validated exact prefix on the App thread."""

        self._sync_persistence_scope(restore=True)
        disposition = self.tracker.record_saved_checkpoint(checkpoint)
        if disposition not in {
            "rejected_saved_checkpoint",
            "rejected_saved_prefix_freshness",
            "rejected_saved_prefix_conflict",
            "ignored_lagging_saved_prefix",
        }:
            self._persist_state()
        return disposition

    def observe_passive(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        wave: Optional[int],
        progress_fn: Callable[[Optional[Frame]], PerkProgress] = (
            lambda frame: measure_perk_progress(frame)
        ),
    ) -> None:
        """Observe top-bar Perk progress while granting no panel input."""

        self.handle(
            screenshot,
            detection,
            wave=wave,
            actions_allowed=False,
            action_guard_fn=lambda: False,
            progress_fn=progress_fn,
        )

    def exhaustion_evidence(self) -> Optional[dict[str, Any]]:
        """Return persisted stable ``View Perks`` evidence, when available."""

        self._sync_persistence_scope(restore=True)
        evidence = self.tracker.snapshot().get("passive_top_bar", {}).get(
            "exhaustion"
        )
        return copy.deepcopy(evidence) if isinstance(evidence, Mapping) else None

    def bind_exhaustion_identity(self, identity: Mapping[str, Any]) -> bool:
        """Persist the save-backed identity on stable exhaustion evidence."""

        self._sync_persistence_scope(restore=True)
        accepted = self.tracker.bind_exhaustion_identity(identity)
        if accepted:
            self._persist_state()
        return accepted

    def _current_scope_id(self) -> Optional[str]:
        if self._state_path is None:
            return None
        try:
            raw_scope_id = self._scope_id_fn()
        except Exception as exc:
            if not self._persistence_warning_active:
                log(
                    "[PERK_TIMELINE] Could not read the current-run identity "
                    f"for checkpointing: {exc}",
                    "WARN",
                )
                self._persistence_warning_active = True
            return None
        normalized = str(raw_scope_id or "").strip()
        return normalized or None

    def _activate_current_scope(self) -> None:
        if self._state_path is None:
            return
        self._active_scope_id = self._current_scope_id()
        self._last_persisted_payload = None

    def _sync_persistence_scope(self, *, restore: bool) -> None:
        if self._state_path is None:
            return
        scope_id = self._current_scope_id()
        if scope_id == self._active_scope_id:
            return
        previous_scope_id = self._active_scope_id
        self._active_scope_id = scope_id
        self._last_persisted_payload = None
        self.tracker.reset(fresh_battle=False)
        self._route_open = False
        self._invalid_progress_count = 0
        self._invalid_progress_warned = False
        self._progress_visibility_interrupted = False
        if scope_id is None:
            return

        payload: Optional[dict[str, Any]] = None
        if restore:
            try:
                payload = _load_perk_timeline_checkpoint(self._state_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log(
                    "[PERK_TIMELINE] Ignoring unreadable persisted tracker "
                    f"state: {exc}",
                    "WARN",
                )
        tracker_payload = payload.get("tracker") if payload is not None else None
        restored = bool(
            payload is not None
            and payload.get("schema_version")
            == PERK_TIMELINE_CHECKPOINT_SCHEMA_VERSION
            and str(payload.get("activity_scope_run_id") or "") == scope_id
            and isinstance(payload.get("route_open"), bool)
            and isinstance(tracker_payload, Mapping)
            and self.tracker.restore_checkpoint(tracker_payload)
        )
        if restored and payload is not None:
            self._route_open = bool(payload["route_open"])
            # Even a clean process replacement leaves an interval during which
            # top-bar schedule changes could not be observed.
            self._progress_visibility_interrupted = True
            presentation_needs_refresh = payload.get(
                "current_perks"
            ) != self.tracker.current_perks_presentation()
            self._last_persisted_payload = copy.deepcopy(payload)
            log(
                "[PERK_TIMELINE] Restored same-run checkpoint "
                f"scope_id={scope_id} batches="
                f"{len(self.tracker.snapshot().get('batches', []))} "
                f"route_open={self._route_open}",
                "INFO",
            )
            if presentation_needs_refresh:
                self._last_persisted_payload = None
                self._persist_state()
            return

        if previous_scope_id is not None and previous_scope_id != scope_id:
            log(
                "[PERK_TIMELINE] Current-run identity changed; starting an "
                f"unknown mid-battle baseline scope_id={scope_id}",
                "INFO",
            )
        self._persist_state()

    def _persist_state(self) -> None:
        if self._state_path is None or self._active_scope_id is None:
            return
        payload = {
            "schema_version": PERK_TIMELINE_CHECKPOINT_SCHEMA_VERSION,
            "activity_scope_run_id": self._active_scope_id,
            "route_open": self._route_open,
            "tracker": self.tracker.checkpoint(),
            "current_perks": self.tracker.current_perks_presentation(),
        }
        if payload == self._last_persisted_payload:
            return
        try:
            _write_perk_timeline_checkpoint(self._state_path, payload)
        except OSError as exc:
            if not self._persistence_warning_active:
                log(
                    "[PERK_TIMELINE] Could not persist same-run tracker state: "
                    f"{exc}",
                    "WARN",
                )
                self._persistence_warning_active = True
            return
        if self._persistence_warning_active:
            log(
                "[PERK_TIMELINE] Same-run tracker checkpoint persistence recovered",
                "INFO",
            )
            self._persistence_warning_active = False
        self._last_persisted_payload = copy.deepcopy(payload)

    def handle(
        self,
        screenshot: Frame,
        detection: Mapping[str, Any],
        *,
        wave: Optional[int],
        actions_allowed: bool,
        action_guard_fn: Callable[[], bool],
        progress_fn: Callable[[Optional[Frame]], PerkProgress] = (
            lambda frame: measure_perk_progress(frame)
        ),
        capture_fn: Capture = capture_adb_screenshot,
        detector: Detector = detect_state_and_overlays,
        safe_tap_fn: Callable[..., bool] = safe_tap,
        tap_visible_fn: Callable[..., bool] = tap_if_visible,
        visible_fn: Callable[..., bool] = is_visible,
        swipe_fn: Callable[[str], bool] = swipe_now,
        full_ocr_fn: Callable[..., Mapping[str, Any]] = ocr_selected_perks,
        rows_ocr_fn: Callable[[Frame], Sequence[Mapping[str, Any]]] = (
            ocr_perk_rows
        ),
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Observe one frame and run a pending panel route when allowed.

        Returns ``True`` after any navigation so the caller discards its stale
        pre-route screenshot.
        """

        self._sync_persistence_scope(restore=True)
        state = str(detection.get("state") or "UNKNOWN")
        if state == "RUNNING":
            progress = progress_fn(screenshot)
            self._record_progress_health(progress)
            self.tracker.observe(
                progress,
                wave=wave,
                boundary_observation_complete=(
                    not self._progress_visibility_interrupted
                ),
                activity_scope_id=self._active_scope_id,
            )
            if (
                self._progress_visibility_interrupted
                and self.tracker.confirmed_progress_resolves_visibility_gap(
                    progress
                )
            ):
                self._progress_visibility_interrupted = False
            self._persist_state()
        elif not (state == "PERKS" and self._route_open):
            self._progress_visibility_interrupted = True
        request = self.tracker.pending
        if request is None:
            if not self._route_open:
                return False
            if state != "PERKS":
                self._route_open = False
                self._persist_state()
                return False
            if not actions_allowed:
                return False
            operation_id = new_operation_id()
            log_action_intent(
                "Restoring the battle view",
                reason="close the perk timeline's completed panel route",
                detail="[PERK_TIMELINE] route_restore=pending",
                operation_id=operation_id,
            )
            if not action_guard_fn():
                log_result(
                    "Perk timeline panel restore paused",
                    detail="[PERK_TIMELINE] route_restore=interrupted",
                    operation_id=operation_id,
                )
                return False
            close_result = _close_perks_panel(
                screenshot=screenshot,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            if not close_result.closed:
                log_result(
                    "Perk timeline panel restore failed",
                    detail=(
                        "[PERK_TIMELINE] route_restore=failed "
                        f"dispatched={close_result.dispatched} "
                        f"observed_state={close_result.observed_state}"
                    ),
                    operation_id=operation_id,
                )
                return close_result.dispatched
            self._route_open = False
            self._persist_state()
            log_result(
                "Battle view restored after perk timeline capture",
                detail=(
                    "[PERK_TIMELINE] route_restore=complete "
                    f"observed_state={close_result.observed_state}"
                ),
                operation_id=operation_id,
            )
            return True
        if not actions_allowed:
            return False
        if state not in {"RUNNING", "PERKS"}:
            return False
        if state == "PERKS" and not self._route_open:
            return False

        reason = (
            "establish a mid-battle selected-Perks baseline"
            if request.kind == "baseline"
            else (
                (
                    "catch up perk selections after the top-bar schedule was "
                    f"unobservable from wave {request.scheduled_wave}"
                )
                if request.boundary_coverage
                != BOUNDARY_COVERAGE_COMPLETE
                else (
                    "record perk selections deferred across scheduled waves "
                    + ", ".join(
                        str(value) for value in request.scheduled_waves
                    )
                    if len(request.scheduled_waves) > 1
                    else (
                        f"record the perk selection scheduled for wave "
                        f"{request.scheduled_wave}"
                    )
                )
            )
        )
        operation_id = new_operation_id()
        log_action_intent(
            "Recording the perk selection timeline",
            reason=reason,
            detail=(
                f"[PERK_TIMELINE] mode={request.snapshot_mode} "
                f"observed_wave={request.observed_wave} "
                f"boundary_coverage={request.boundary_coverage}"
            ),
            operation_id=operation_id,
        )

        navigated = state == "PERKS"
        current = screenshot
        try:
            if state == "RUNNING":
                if not action_guard_fn():
                    raise _RouteInterrupted("control no longer allows inputs")
                if not safe_tap_fn(
                    "navigation.open_perks",
                    dispatch="now",
                    log_label="perk_timeline:open",
                    screenshot=screenshot,
                    failure_log_level="DEBUG",
                ):
                    raise _RouteFailed("verified Perks control was unavailable")
                self._route_open = True
                self._persist_state()
                navigated = True
                current = _wait_for_perks(
                    capture_fn=capture_fn,
                    detector=detector,
                    sleep_fn=sleep_fn,
                )

            capture_ok, completed_request = self._capture_pending(
                current,
                action_guard_fn=action_guard_fn,
                progress_fn=progress_fn,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=swipe_fn,
                full_ocr_fn=full_ocr_fn,
                rows_ocr_fn=rows_ocr_fn,
                sleep_fn=sleep_fn,
            )
            refreshed = capture_fn()
            if refreshed is not None:
                current = refreshed
            if not action_guard_fn():
                raise _RouteInterrupted("control changed before panel restore")
            close_result = _close_perks_panel(
                screenshot=current,
                capture_fn=capture_fn,
                detector=detector,
                tap_visible_fn=tap_visible_fn,
                sleep_fn=sleep_fn,
            )
            if not close_result.dispatched:
                raise _RouteFailed("verified Perks close control was unavailable")
            if not close_result.closed:
                raise _RouteFailed(
                    "Perks panel remained visible after the verified close input"
                )
            self._route_open = False
            self._persist_state()
            latest_batch = self.tracker.latest_batch if capture_ok else None
            selections = (
                latest_batch.get("selections", [])
                if isinstance(latest_batch, Mapping)
                else []
            )
            selection_labels = [
                str(selection.get("display_text") or "unknown")
                for selection in selections
                if isinstance(selection, Mapping)
            ]
            if capture_ok and completed_request.kind == "baseline":
                summary = "Perk timeline baseline recorded"
            elif (
                capture_ok
                and completed_request.boundary_coverage
                != BOUNDARY_COVERAGE_COMPLETE
            ):
                summary = (
                    "Perk timeline interval recorded after an unobserved "
                    f"schedule gap from wave {completed_request.scheduled_wave}"
                    + (
                        " — " + ", ".join(selection_labels)
                        if selection_labels
                        else ""
                    )
                )
            elif capture_ok:
                summary = _recorded_selection_summary(
                    selection_labels,
                    all_selected=(
                        completed_request.progress_after.status == "complete"
                    ),
                    scheduled_waves=completed_request.scheduled_waves,
                )
            else:
                summary = "Perk timeline observation will be retried"
            log_result(
                summary,
                detail=(
                    f"[PERK_TIMELINE] result="
                    f"{'recorded' if capture_ok else 'retry'} "
                    f"scheduled_wave={completed_request.scheduled_wave} "
                    f"scheduled_waves="
                    f"{list(completed_request.scheduled_waves)} "
                    f"boundary_coverage="
                    f"{completed_request.boundary_coverage} "
                    f"selection_count={len(selection_labels)} "
                    f"close_state={close_result.observed_state}"
                    f"{_perk_activity_data(selection_labels) if capture_ok else ''}"
                ),
                operation_id=operation_id,
            )
            return True
        except _RouteInterrupted as exc:
            log_result(
                "Perk timeline observation paused",
                detail=f"[PERK_TIMELINE] result=interrupted reason={exc}",
                operation_id=operation_id,
            )
            return navigated
        except _RouteFailed as exc:
            log_result(
                "Perk timeline observation failed",
                detail=f"[PERK_TIMELINE] result=failed reason={exc}",
                operation_id=operation_id,
            )
            return navigated
        except Exception as exc:
            log(
                f"[PERK_TIMELINE] Unexpected panel capture failure: {exc}",
                "ERROR",
            )
            log_result(
                "Perk timeline observation failed",
                detail=f"[PERK_TIMELINE] result=failed error={exc}",
                operation_id=operation_id,
            )
            return navigated

    def _record_progress_health(self, progress: PerkProgress) -> None:
        """Report persistent invalid OCR while continuing read-only retries."""

        if progress.status != "invalid_schedule":
            if self._invalid_progress_warned:
                log(
                    "[PERK_TIMELINE] Top-bar schedule recovered after "
                    f"{self._invalid_progress_count} invalid observation(s)",
                    "INFO",
                )
            self._invalid_progress_count = 0
            self._invalid_progress_warned = False
            return

        self._invalid_progress_count += 1
        log(
            "[PERK_TIMELINE] Ignoring implausible top-bar schedule "
            f"current={progress.current_wave} next={progress.next_wave} "
            f"raw={progress.text_raw!r} "
            f"attempt={self._invalid_progress_count}",
            "DEBUG",
        )
        if (
            self._invalid_progress_count >= INVALID_PROGRESS_WARNING_FRAMES
            and not self._invalid_progress_warned
        ):
            self._invalid_progress_warned = True
            log(
                "[PERK_TIMELINE] Top-bar schedule remained implausible for "
                f"{self._invalid_progress_count} observations; timeline "
                "capture is retrying without device input",
                "WARN",
            )

    def _capture_pending(
        self,
        screenshot: Frame,
        *,
        action_guard_fn: Callable[[], bool],
        progress_fn: Callable[[Optional[Frame]], PerkProgress],
        capture_fn: Capture,
        visible_fn: Callable[..., bool],
        swipe_fn: Callable[[str], bool],
        full_ocr_fn: Callable[..., Mapping[str, Any]],
        rows_ocr_fn: Callable[[Frame], Sequence[Mapping[str, Any]]],
        sleep_fn: Callable[[float], None],
    ) -> tuple[bool, PerkCaptureRequest]:
        def guarded_swipe_fn(key: str) -> bool:
            if not action_guard_fn():
                raise _RouteInterrupted("control changed before panel swipe")
            return swipe_fn(key)

        top = scroll_to_edge(
            "gesture_targets.goto_top:perks",
            source_label=PERKS_INDICATOR,
            screenshot=screenshot,
            progress_region=PERKS_CONTENT_REGION,
            max_swipes=8,
            settle_s=0.8,
            capture_fn=capture_fn,
            visible_fn=visible_fn,
            swipe_fn=guarded_swipe_fn,
            sleep_fn=sleep_fn,
        )
        if top.screenshot is None or not top.success:
            raise _RouteFailed(f"could not reach Perks top ({top.reason})")

        top_frame = self._refresh_progress_from_panel(
            top.screenshot,
            progress_fn=progress_fn,
            capture_fn=capture_fn,
            sleep_fn=sleep_fn,
        )
        request = self.tracker.pending
        if request is None:
            raise _RouteFailed("Perk timeline request disappeared during capture")

        for capture_attempt in range(2):
            request_before = self.tracker.pending
            if request_before is None:
                raise _RouteFailed(
                    "Perk timeline request disappeared during full capture"
                )
            stop_fn = None
            if request_before.kind == "selection":
                def stop_at_unchanged(frame: Frame) -> Optional[str]:
                    if any(
                        self.tracker.selection_is_unchanged(row)
                        for row in rows_ocr_fn(frame)
                    ):
                        return "unchanged_timeline_row"
                    return None

                stop_fn = stop_at_unchanged
            capture = capture_scroll_to_edge(
                "gesture_targets.goto_next:perks",
                source_label=PERKS_INDICATOR,
                screenshot=top_frame,
                progress_region=PERKS_CONTENT_REGION,
                max_swipes=20,
                settle_s=0.8,
                capture_fn=capture_fn,
                visible_fn=visible_fn,
                swipe_fn=guarded_swipe_fn,
                sleep_fn=sleep_fn,
                stop_fn=stop_fn,
            )
            progress_frame = (
                capture.screenshots[-1]
                if capture.screenshots
                else top_frame
            )
            settled_frame = self._refresh_progress_from_panel(
                progress_frame,
                progress_fn=progress_fn,
                capture_fn=capture_fn,
                sleep_fn=sleep_fn,
            )
            request_after = self.tracker.pending
            if request_after is None:
                raise _RouteFailed(
                    "Perk timeline request disappeared after full capture"
                )
            if (
                request_after.progress_after.token
                != request_before.progress_after.token
            ):
                if capture_attempt == 1:
                    return False, request_after
                top = scroll_to_edge(
                    "gesture_targets.goto_top:perks",
                    source_label=PERKS_INDICATOR,
                    screenshot=settled_frame,
                    progress_region=PERKS_CONTENT_REGION,
                    max_swipes=8,
                    settle_s=0.8,
                    capture_fn=capture_fn,
                    visible_fn=visible_fn,
                    swipe_fn=guarded_swipe_fn,
                    sleep_fn=sleep_fn,
                )
                if top.screenshot is None or not top.success:
                    raise _RouteFailed(
                        "could not repeat Perks capture after the schedule "
                        f"advanced ({top.reason})"
                    )
                top_frame = self._refresh_progress_from_panel(
                    top.screenshot,
                    progress_fn=progress_fn,
                    capture_fn=capture_fn,
                    sleep_fn=sleep_fn,
                )
                continue

            full = full_ocr_fn(
                list(capture.screenshots),
                source_complete=capture.success,
                source_reason=capture.reason,
            )
            if capture.reason == "unchanged_timeline_row":
                recorded = self.tracker.record_snapshot_to_unchanged(full)
                self._persist_state()
                if recorded:
                    return (
                        True,
                        self.tracker.last_completed_request or request_after,
                    )
                selected_rows = full.get("selected")
                if (
                    isinstance(selected_rows, Sequence)
                    and not isinstance(selected_rows, (str, bytes))
                    and any(
                        isinstance(row, Mapping)
                        and self.tracker.selection_is_unchanged(row)
                        for row in selected_rows
                    )
                ):
                    return False, request_after
                fallback = capture_scroll_to_edge(
                    "gesture_targets.goto_next:perks",
                    source_label=PERKS_INDICATOR,
                    screenshot=(
                        capture.screenshots[-1]
                        if capture.screenshots
                        else settled_frame
                    ),
                    progress_region=PERKS_CONTENT_REGION,
                    max_swipes=20,
                    settle_s=0.8,
                    capture_fn=capture_fn,
                    visible_fn=visible_fn,
                    swipe_fn=guarded_swipe_fn,
                    sleep_fn=sleep_fn,
                )
                combined = list(capture.screenshots)
                if fallback.screenshots:
                    combined.extend(fallback.screenshots[1:])
                full = full_ocr_fn(
                    combined,
                    source_complete=fallback.success,
                    source_reason=fallback.reason,
                )
            recorded = bool(self.tracker.record_full_snapshot(full))
            self._persist_state()
            completed_request = request_after
            if recorded and self.tracker.last_completed_request is not None:
                completed_request = self.tracker.last_completed_request
            return recorded, completed_request

        raise _RouteFailed("Perk timeline full capture retry was exhausted")

    def _refresh_progress_from_panel(
        self,
        screenshot: Frame,
        *,
        progress_fn: Callable[[Optional[Frame]], PerkProgress],
        capture_fn: Capture,
        sleep_fn: Callable[[float], None],
    ) -> Frame:
        """Refresh pending schedule evidence while the modal route owns input."""

        current = screenshot
        progress = progress_fn(current)
        self.tracker.observe(
            progress,
            wave=progress.current_wave,
            boundary_observation_complete=(
                not self._progress_visibility_interrupted
            ),
            activity_scope_id=self._active_scope_id,
        )
        if (
            self._progress_visibility_interrupted
            and self.tracker.confirmed_progress_resolves_visibility_gap(
                progress
            )
        ):
            self._progress_visibility_interrupted = False
        self._persist_state()
        for _ in range(2):
            sleep_fn(0.25)
            refreshed = capture_fn()
            if refreshed is None:
                continue
            current = refreshed
            progress = progress_fn(current)
            self.tracker.observe(
                progress,
                wave=progress.current_wave,
                boundary_observation_complete=(
                    not self._progress_visibility_interrupted
                ),
                activity_scope_id=self._active_scope_id,
            )
            if (
                self._progress_visibility_interrupted
                and self.tracker.confirmed_progress_resolves_visibility_gap(
                    progress
                )
            ):
                self._progress_visibility_interrupted = False
            self._persist_state()
        return current


def measure_perk_progress(
    screenshot: Optional[Frame],
    *,
    text_fn: Callable[[Frame], tuple[str, float]] = (
        lambda crop: ocr_text_and_conf(crop, psm=7)
    ),
) -> PerkProgress:
    """Read ``current wave / next perk wave`` or the terminal View Perks label."""

    if screenshot is None or screenshot.ndim < 2:
        return PerkProgress("unreadable", None, None, "", -1.0)
    x, y, width, height = PERK_PROGRESS_TEXT_REGION
    crop = screenshot[y : y + height, x : x + width]
    if crop.size == 0:
        return PerkProgress("unreadable", None, None, "", -1.0)
    observed_at = datetime.now(timezone.utc).isoformat()
    source_fingerprint = hashlib.sha256(crop.tobytes()).hexdigest()
    enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    raw_text, confidence = text_fn(enlarged)
    normalized = " ".join(str(raw_text or "").split())
    upper = normalized.upper()
    if "VIEW" in upper and "PERK" in upper:
        return PerkProgress(
            "complete",
            None,
            None,
            normalized,
            float(confidence),
            observed_at,
            source_fingerprint,
        )
    if "NEW" in upper and "PERK" in upper:
        return PerkProgress(
            "selection_pending",
            None,
            None,
            normalized,
            float(confidence),
            observed_at,
            source_fingerprint,
        )
    numbers = [
        int(value)
        for value in re.findall(r"(?<!\d)(\d{1,6})(?!\d)", normalized)
    ]
    if len(numbers) >= 2:
        current_wave = numbers[0]
        next_wave = numbers[-1]
        status = (
            "scheduled"
            if _scheduled_progress_is_plausible(current_wave, next_wave)
            else "invalid_schedule"
        )
        return PerkProgress(
            status,
            current_wave,
            next_wave,
            normalized,
            float(confidence),
            observed_at,
            source_fingerprint,
        )
    return PerkProgress(
        "unreadable",
        None,
        None,
        normalized,
        float(confidence),
        observed_at,
        source_fingerprint,
    )


def _scheduled_progress_is_plausible(
    current_wave: Optional[int],
    next_wave: Optional[int],
) -> bool:
    if current_wave is None or next_wave is None:
        return False
    return 0 < next_wave - current_wave <= MAX_PERK_SCHEDULE_LEAD_WAVES


def timeline_perk_family(text: Any) -> str:
    """Return a stable family for every currently known selected-Perk label."""

    normalized = _comparison_text(str(text or ""))
    if "coins" in normalized and "tower max health" in normalized:
        return "coin_tradeoff"
    if "enemies have" in normalized and "tower health regen" in normalized:
        return "enemy_health_tradeoff"
    if "tower damage" in normalized and "bosses have" in normalized:
        return "tower_damage_boss_health_tradeoff"
    canonical = canonical_perk_family(text)
    if canonical is not None:
        return canonical
    ordered_contains = (
        ("cash per wave", "cash_wave_tradeoff"),
        ("enemies speed", "enemy_speed_tradeoff"),
        ("ranged enemies attack distance", "ranged_distance_tradeoff"),
        ("bounce shot", "bounce_shot"),
    )
    value_free = re.sub(r"\b\d+(?:\.\d+)?\b", "", normalized)
    value_free = re.sub(r"\s+", " ", value_free).strip()
    for fragment, family in ordered_contains:
        if fragment in value_free:
            return family
    return re.sub(r"[^a-z0-9]+", "_", value_free).strip("_") or "unknown"


def _index_selected_perks(
    selected: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in selected:
        if not isinstance(raw, Mapping):
            continue
        entry = _timeline_entry(raw)
        if entry is not None:
            indexed[entry["family"]] = entry
    return indexed


def _mapping_evidence_from_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Strip a timeline batch to the fields needed for ID calibration."""

    raw_selections = batch.get("selections")
    selections = []
    if isinstance(raw_selections, Sequence) and not isinstance(
        raw_selections,
        (str, bytes, bytearray),
    ):
        for raw in raw_selections:
            if not isinstance(raw, Mapping):
                continue
            classified_family = classify_perk_configuration_text(
                str(raw.get("display_text") or "")
            )
            selections.append(
                {
                    "family": classified_family
                    or str(raw.get("family") or ""),
                    "confidence_percent": raw.get("confidence"),
                    "change": str(raw.get("change") or ""),
                }
            )
    return {
        "schema_version": 1,
        "sequence": batch.get("sequence"),
        "scheduled_wave": batch.get("scheduled_wave"),
        "scheduled_waves": copy.deepcopy(batch.get("scheduled_waves")),
        "boundary_coverage": batch.get("boundary_coverage"),
        "selection_model": batch.get("selection_model"),
        "observed_at": batch.get("observed_at"),
        "selections": selections,
    }


def _timeline_entry(perk: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    display = " ".join(str(perk.get("display_text") or "").split())
    if not display:
        return None
    return {
        "family": timeline_perk_family(display),
        "display_text": display,
        "color": str(perk.get("color") or "unknown"),
        "instance_model": str(perk.get("instance_model") or "unknown"),
        "confidence": float(perk.get("confidence") or -1.0),
    }


def _diff_selected_perks(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    changes = []
    for family, current in after.items():
        previous = before.get(family)
        if previous is None or not _same_display(previous, current):
            changes.append(_selection_change(previous, current))
    return changes


def _selection_change(
    before: Optional[Mapping[str, Any]],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    change = copy.deepcopy(dict(after))
    change["change"] = "added" if before is None else "level_changed"
    if before is not None:
        change["before_display_text"] = str(before.get("display_text") or "")
    return change


def _same_display(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return _comparison_text(str(left.get("display_text") or "")) == (
        _comparison_text(str(right.get("display_text") or ""))
    )


def _snapshot_has_max_pwr(
    selected: Mapping[str, Mapping[str, Any]],
) -> bool:
    entry = selected.get(PWR_FAMILY)
    return bool(
        entry
        and PWR_MAX_PATTERN.search(str(entry.get("display_text") or ""))
    )


def _comparison_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9.%+-]+", " ", text.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _wait_for_perks(
    *,
    capture_fn: Capture,
    detector: Detector,
    sleep_fn: Callable[[float], None],
    attempts: int = 8,
) -> Frame:
    for _ in range(max(1, attempts)):
        sleep_fn(0.4)
        frame = capture_fn()
        if frame is not None and detector(frame).get("state") == "PERKS":
            return frame
    raise _RouteFailed("Perks panel did not become visible")


def _close_perks_panel(
    screenshot: Frame,
    *,
    capture_fn: Capture,
    detector: Detector,
    tap_visible_fn: Callable[..., bool],
    sleep_fn: Callable[[float], None],
    attempts: int = 8,
) -> _PanelCloseResult:
    """Dispatch one verified close and require an authoritative destination."""

    if not tap_visible_fn(
        "buttons.close:perks",
        screenshot=screenshot,
        retries=1,
    ):
        return _PanelCloseResult(
            dispatched=False,
            closed=False,
            observed_state="PERKS",
        )

    observed_state = "UNKNOWN"
    for _ in range(max(1, attempts)):
        sleep_fn(0.4)
        frame = capture_fn()
        if frame is None:
            continue
        observed_state = str(detector(frame).get("state") or "UNKNOWN")
        if observed_state in PERKS_CLOSE_DESTINATIONS:
            return _PanelCloseResult(
                dispatched=True,
                closed=True,
                observed_state=observed_state,
            )
    return _PanelCloseResult(
        dispatched=True,
        closed=False,
        observed_state=observed_state,
    )


class _RouteFailed(RuntimeError):
    pass


class _RouteInterrupted(RuntimeError):
    pass


__all__ = [
    "PerkCaptureRequest",
    "PerkProgress",
    "PerkTimelineObserver",
    "PerkTimelineTracker",
    "measure_perk_progress",
    "timeline_perk_family",
]
