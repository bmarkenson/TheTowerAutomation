from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re

import numpy as np
import pytest

from core.battle_stats import (
    attach_observed_run_configuration,
    attach_battle_perks,
    build_battle_record,
    build_battle_record_from_clipboard,
    build_battle_record_from_player_save,
    build_minimal_battle_record_from_player_save,
    format_tower_number,
    included_in_default_history,
    parse_more_stats_clipboard,
    parse_duration_seconds,
    parse_tower_number,
    persist_battle_record,
    render_battle_markdown,
    render_perk_selection_timeline_markdown,
    render_survival_ability_activations_markdown,
)
from utils.previous_wave import get_previous_run_wave


CLIPBOARD_REPORT_PATH = Path(__file__).parent / "fixtures" / "battle_report_clipboard.txt"
VERSION_MAPPING = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "config/player_save_versions/data_9_game_1073.json"
    ).read_text(encoding="utf-8")
)


def _terminal_save_report() -> dict:
    history = VERSION_MAPPING["runtime_save"]["battle_history"]
    values = {
        "gameTime": 20599,
        "realTime": 4244,
        "tier": 19,
        "wave": 2558,
        "killedBy": "Scatter",
        "coinsEarned": 872_380_000_000_000_000,
        "cellsEarned": 204_600,
        "totalEnemies": 160_757,
    }
    sections = []
    for section_spec in history["more_stats_sections"]:
        section_name = section_spec["name"]
        section_key = _slug(section_name)
        rows = []
        for label, row_spec in section_spec["rows"]:
            row = {
                "section": section_name,
                "section_key": section_key,
                "label": label,
                "key": _slug(label),
                "source": "player_save_battle_history",
            }
            if isinstance(row_spec, str):
                source_fields = [row_spec]
                value = values.get(row_spec, 1)
                row.update(
                    {
                        "value_type": "number",
                        "value": value,
                        "value_decimal": str(value),
                        "source_fields": source_fields,
                        "derivation": "direct",
                    }
                )
            elif row_spec.get("kind") == "killed_by_enum":
                row.update(
                    {
                        "value_type": "text",
                        "value": "Scatter",
                        "enum_id": 8,
                        "source_fields": [row_spec["source"]],
                        "derivation": "versioned_enum",
                    }
                )
            elif row_spec.get("derive") == "per_real_hour":
                amount = values.get(row_spec["amount"], 1)
                rate = Decimal(amount) * Decimal(3600) / Decimal(values["realTime"])
                row.update(
                    {
                        "value_type": "rate_per_real_hour_decimal",
                        "value_decimal": str(rate),
                        "source_fields": [row_spec["amount"], row_spec["seconds"]],
                        "derivation": "amount_per_real_hour",
                    }
                )
            else:
                source = row_spec["source"]
                value = values.get(source, 1)
                row.update(
                    {
                        "value_type": row_spec.get("kind", "number"),
                        "value": value,
                        "value_decimal": str(value),
                        "source_fields": [source],
                        "derivation": "direct",
                    }
                )
                active_field = row_spec.get("active_percent_of")
                if active_field:
                    row["source_fields"].append(active_field)
                    row["derivation"] = "direct_with_active_percent"
                    row["active_percent_decimal"] = "0.000622"
            rows.append(row)
        sections.append({"name": section_name, "key": section_key, "rows": rows})

    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "reason": "",
        "terminal_state": "GAME_OVER",
        "mapping_id": "data-9-game-1073",
        "capture": {
            "captured_at": "2026-08-06T11:00:00+00:00",
            "save_revision": 48000,
            "source_fingerprint": "d" * 64,
        },
        "history_transition": {"status": "capacity_rollover"},
        "completed_entry": {
            "schema_version": 1,
            "mapping_id": "data-9-game-1073",
            "identity": {
                "tier": 19,
                "wave": 2558,
                "is_tournament": False,
            },
            "more_stats": {
                "source_method": "player_save_battle_history",
                "source_complete": True,
                "row_count": 144,
                "sections": sections,
            },
            "fingerprint": "c" * 64,
        },
        "ui_fallback": {"required": False, "reason": ""},
    }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _frame(page: int) -> np.ndarray:
    return np.full((1920, 1080, 3), page, dtype=np.uint8)


def _ocr_data(lines):
    data = {
        key: []
        for key in (
            "text",
            "conf",
            "left",
            "top",
            "width",
            "height",
            "block_num",
            "par_num",
            "line_num",
        )
    }
    for line_number, (label, value, confidence) in enumerate(lines, start=1):
        is_header = value is None
        left = 300 if is_header else 40
        for token in label.split():
            _append_token(data, token, confidence, left, line_number)
            left += len(token) * 18 + 16
        if value is not None:
            left = 620
            for token in value.split():
                _append_token(data, token, confidence, left, line_number)
                left += len(token) * 18 + 16
    return data


def _append_token(data, text, confidence, left, line_number):
    data["text"].append(text)
    data["conf"].append(confidence)
    data["left"].append(left)
    data["top"].append(20 + line_number * 55)
    data["width"].append(max(15, len(text) * 17))
    data["height"].append(35)
    data["block_num"].append(1)
    data["par_num"].append(1)
    data["line_num"].append(line_number)


PAGES = {
    1: _ocr_data(
        [
            ("Battle Report", None, 96),
            ("Battle Date", "Jul 15, 2026 06:46", 95),
            ("Game Time", "5h 0m 0s", 95),
            ("Real Time", "1h 0m 0s", 95),
            ("Tier", "19", 95),
            ("Wave", "2000", 95),
            ("Killed By", "Scatter", 95),
            ("Coins Earned", "2.00q", 95),
            ("Coins Per Hour", "2.00q", 95),
            ("Cells Earned", "100.00K", 95),
            ("Cells Per Hour", "100.00K", 95),
            ("Damage", None, 96),
            ("Damage Dealt", "blur", 20),
        ]
    ),
    2: _ocr_data(
        [
            ("Damage Dealt", "17.04ab", 96),
            ("Projectiles", "30.14aa", 95),
            ("Smart Missiles", "4.8laa", 55),
            ("Damage Taken", None, 96),
            ("Tower", "434.09Q", 95),
            ("Wall", "705.94Q", 95),
            ("Currencies", None, 96),
            ("Cells Earned", "100.00K", 95),
            ("Gems", "36", 95),
            ("Ad Gems", "24", 95),
            ("Gem Blocks Tapped", "2", 95),
            ("Fetch Gems", "3", 95),
            ("Medals", "1", 95),
            ("Reroll Shards Earned", "72.00K", 95),
            ("Reroll Shards Fetched", "0)", 95),
            ("Cannon Shards", "10", 95),
            ("Armor Shards", "20", 95),
            ("Generator Shards", "30", 95),
            ("Core Shards", "40", 95),
            ("Common Modules", "4", 95),
            ("Rare Modules", "2", 95),
            ("Future Stat Added By The Game", "7", 95),
        ]
    ),
    3: _ocr_data(
        [
            ("Common Modules", "4", 95),
            ("Rare Modules", "2", 95),
            ("Future Stat Added By The Game", "7", 95),
            ("Killed With Effect Active", None, 96),
            ("Golden Tower 160698", "[100.0%]", 95),
            ("Amplify Bot", "0", 95),
            ("Enemies Destroyed By", None, 96),
            ("Projectiles", "40", 95),
            ("Other", "138", 95),
        ]
    ),
    4: _ocr_data(
        [
            ("Records", None, 96),
            ("Record Example", "1", 95),
            ("Bonus Health Gained", None, 96),
            ("From Death Wave", "0", 95),
            ("Health Regenerated", None, 96),
            ("Lifesteal", "1", 95),
            ("Damage Blocked", None, 96),
            ("Defense Absolute", "1", 95),
            ("Utility", None, 96),
            ("Recovery Packages", "1", 95),
            ("Counts", None, 96),
            ("Projectiles Count", "1", 95),
        ]
    ),
    5: _ocr_data(
        [
            ("Enemies Hit By", None, 96),
            ("Projectiles", "1", 95),
            ("Total Enemies", None, 96),
            ("Basic", "1", 95),
            ("Coins", None, 96),
            ("Coins Earned", "2.00q", 95),
            ("Cash", None, 96),
            ("Cash Earned", "$1.00K", 95),
        ]
    ),
}


def _data_fn(frame):
    return PAGES[int(frame[0, 0, 0])]


def _game_text(_frame, *, psm):
    assert psm == 6
    return (
        "GAME STATS Wave 2000 Tier 19 Highest Wave: 5575 "
        "Killed By Scatter Death defied 12 times coins earned ad coins earned "
        "total coins 1.00q + 1.00q = 2.00q",
        95.0,
    )


def _record(
    *,
    source_complete=True,
    source_reason="edge_reached",
    profile_progression=None,
):
    return build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4), _frame(5)],
        source_complete=source_complete,
        source_reason=source_reason,
        battle_id="Battle20260715T120000-0700",
        captured_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        strategy_name="gc_farm_t19_experiment",
        run_configuration={
            "schema_version": 2,
            "profile": "farm",
            "tier": 19,
            "settings": {
                "cards_deck": "Farm",
                "card_recharge_modes": {
                    "Demon Mode": "auto_reactivate",
                    "Nuke": "ready_after_recharge",
                },
                "workshop_preset": "Farm",
                "bots_preset": "Farm",
                "guardian_chips": ["Fetch", "Summon", "Scout"],
                "auto_pick_perks": True,
                "ultimate_weapons": {
                    "Poison Swamp": {"primary": "on", "stun": "off"},
                },
            },
            "loadout": {
                "modules": {
                    "mode": "observe",
                    "preset": "module_test_a",
                    "resolved": {"core_primary": "Multiverse Nexus"},
                },
                "damage_slider": {"mode": "preserve"},
                "orb_distance": {
                    "mode": "observe",
                    "preset": "farm_min_range",
                    "resolved": {
                        "range_basis": "30.00m",
                        "extra": "30.00m",
                        "workshop": "39.00m",
                    },
                },
                "target_priority": {
                    "mode": "observe",
                    "preset": "priority_test_a",
                    "resolved": ["Fleets", "Boss"],
                },
            },
        },
        runtime_context={
            "last_wave": 42,
            "terminal_state": "GAME_OVER",
            "coin_rate_samples": [
                {
                    "captured_at": "2026-07-15T11:00:00-07:00",
                    "wave": 1000,
                    "coins_per_minute_decimal": "1250000000000",
                    "display": "1.25T",
                    "confidence": 98.5,
                }
            ],
            "survival_ability_activations": {
                "schema_version": 4,
                "source": "visual_transition_detection",
                "second_wind_activations": [
                    {
                        "ability": "second_wind",
                        "sequence": 1,
                        "approximate_wave": 4190,
                        "estimated_rearm_wave": 4590,
                        "detected_at": "2026-07-15T11:29:30-07:00",
                    },
                    {
                        "ability": "second_wind",
                        "sequence": 2,
                        "approximate_wave": 4590,
                        "estimated_rearm_wave": 4990,
                        "detected_at": "2026-07-15T11:39:30-07:00",
                    },
                ],
                "demon_mode_first_activation": {
                    "ability": "demon_mode",
                    "sequence": 1,
                    "approximate_wave": 4210,
                    "detected_at": "2026-07-15T11:30:00-07:00",
                },
                "nuke_activations": [
                    {
                        "ability": "nuke",
                        "sequence": 1,
                        "approximate_wave": 4211,
                        "detected_at": "2026-07-15T11:30:02-07:00",
                    },
                    {
                        "ability": "nuke",
                        "sequence": 2,
                        "approximate_wave": 4611,
                        "detected_at": "2026-07-15T11:40:02-07:00",
                    },
                ],
            },
            **(
                {"profile_progression": profile_progression}
                if profile_progression is not None
                else {}
            ),
        },
        data_fn=_data_fn,
        game_stats_text_fn=_game_text,
    )


def _progression(revision: int, *, menu_unlocked: bool) -> dict:
    values = {
        "tower_unlocked": [True, False],
        "background_unlocked": [True, False],
        "menu_unlocked": [True, menu_unlocked],
    }
    return {
        "schema_version": 1,
        "status": "complete",
        "complete": True,
        "identity": {
            "data_version": 9,
            "game_version": 1073,
            "save_revision": revision,
            "mapping_id": "data-9-game-1073",
            "audit_matrix_id": "data-9-game-1073-profile-progression-v1",
        },
        "source": {
            "captured_at": f"2026-07-15T12:0{revision}:00+00:00",
            "sha256": f"sha-{revision}",
        },
        "fingerprint": f"fingerprint-{revision}",
        "components": {
            "themes": {
                "status": "structural",
                "complete": True,
                "source_fields": [
                    "towerUnlocked",
                    "backgroundUnlocked",
                    "menuUnlocked",
                ],
                "values": values,
                "summary": {
                    "tower_unlocked": {"length": 2, "true_count": 1},
                    "background_unlocked": {"length": 2, "true_count": 1},
                    "menu_unlocked": {
                        "length": 2,
                        "true_count": 2 if menu_unlocked else 1,
                    },
                },
                "fingerprint": f"themes-{revision}",
            }
        },
        "warnings": [],
    }


def test_battle_record_retains_resolved_run_configuration():
    record = _record()

    assert record["schema_version"] == 6
    assert record["battle_type"] == "farm"
    assert record["battle_type_analysis"]["confidence"] == "high"
    assert record["runtime"]["observed_tier"] == 19
    assert record["battle_type_analysis"]["observed_tier"] == 19
    assert record["run_configuration"]["profile"] == "farm"
    assert record["run_configuration"]["tier"] == 19
    assert (
        record["run_configuration"]["loadout"]["modules"]["preset"]
        == "module_test_a"
    )
    markdown = render_battle_markdown(record)
    assert "Run configuration: farm Tier 19" in markdown
    assert "Observed tier: 19" in markdown
    assert "Modules: mode `observe`; preset `module_test_a`" in markdown
    assert "core_primary=Multiverse Nexus" in markdown
    assert "Orb Distance: mode `observe`; preset `farm_min_range`" in markdown
    assert (
        "resolved: range_basis=30.00m; extra=30.00m; workshop=39.00m"
        in markdown
    )
    assert "Target Priority: mode `observe`; preset `priority_test_a`" in markdown
    assert "resolved: Fleets > Boss" in markdown
    assert "Bots preset: Farm" in markdown
    assert (
        "Card recharge modes: Demon Mode=auto_reactivate, "
        "Nuke=ready_after_recharge"
    ) in markdown
    assert "Guardian chips: Fetch > Summon > Scout" in markdown
    assert "Auto Pick Perks: enabled" in markdown
    assert "Poison Swamp: primary=on, stun=off" in markdown
    assert "## Coins/min progression" in markdown
    assert "| 2026-07-15T11:00:00-07:00 | 1000 | 1.25T | 98.5% |" in markdown
    assert "## Survival ability activations" in markdown
    assert "| 1 | 4190 | 4590 | 2026-07-15T11:29:30-07:00 |" in markdown
    assert "| 2 | 4590 | 4990 | 2026-07-15T11:39:30-07:00 |" in markdown
    assert "Demon Mode first activation: approximately wave 4210" in markdown
    assert "| 1 | 4211 | 2026-07-15T11:30:02-07:00 |" in markdown
    assert "| 2 | 4611 | 2026-07-15T11:40:02-07:00 |" in markdown


def test_survival_activation_markdown_distinguishes_legacy_and_observed_none():
    legacy = render_survival_ability_activations_markdown(
        {
            "schema_version": 2,
            "demon_mode_first_activation": {
                "approximate_wave": 3000,
            },
            "nuke_activations": [],
        }
    )
    observed_none = render_survival_ability_activations_markdown(
        {
            "schema_version": 4,
            "second_wind_activations": [],
            "demon_mode_first_activation": None,
            "nuke_activations": [],
        }
    )

    assert not any("Second Wind" in line for line in legacy)
    assert "- Second Wind activations: none observed" in observed_none


def test_render_perk_selection_timeline_preserves_atomic_batches():
    lines = render_perk_selection_timeline_markdown(
        {
            "baseline_status": "new_battle_empty",
            "pwr_maxed_observed": True,
            "batches": [
                {
                    "sequence": 1,
                    "scheduled_wave": 100,
                    "observed_wave": 101,
                    "selections": [
                        {
                            "display_text": "Perk wave requirement -25.00%",
                        },
                        {
                            "display_text": "Increase max game speed by +0.50",
                        },
                    ],
                },
                {
                    "sequence": 2,
                    "scheduled_wave": 142,
                    "observed_wave": 143,
                    "selections": [
                        {
                            "before_display_text": "Defense percent +5.00%",
                            "display_text": "Defense percent +10.00%",
                        }
                    ],
                },
            ],
            "warnings": [],
            "pending_scheduled_wave": None,
        }
    )

    rendered = "\n".join(lines)
    assert "same row were observed as one simultaneous batch" in rendered
    assert (
        "Perk wave requirement -25.00%; Increase max game speed by +0.50"
        in rendered
    )
    assert "Defense percent +5.00% → Defense percent +10.00%" in rendered
    assert "Perk Wave Requirement −75% observed: yes" in rendered


def test_render_perk_selection_timeline_marks_pause_interval_aggregates():
    lines = render_perk_selection_timeline_markdown(
        {
            "baseline_status": "new_battle_empty",
            "pwr_maxed_observed": False,
            "batches": [
                {
                    "sequence": 1,
                    "scheduled_wave": 665,
                    "scheduled_waves": [665, 760, 855],
                    "observed_wave": 690,
                    "observed_wave_end": 880,
                    "selection_model": "interval_aggregate",
                    "selections": [
                        {"display_text": "x1.44 all coins bonuses"},
                        {"display_text": "Orbs +2"},
                    ],
                }
            ],
            "warnings": [],
            "pending_scheduled_wave": None,
        }
    )

    rendered = "\n".join(lines)
    assert "665, 760, 855 (interval aggregate)" in rendered
    assert "| 690–880 |" in rendered
    assert "Ambiguous diffs remain interval aggregates" in rendered


def test_render_perk_timeline_marks_unobserved_boundary_interval():
    lines = render_perk_selection_timeline_markdown(
        {
            "baseline_status": "new_battle_empty",
            "pwr_maxed_observed": True,
            "batches": [
                {
                    "sequence": 1,
                    "scheduled_wave": 142,
                    "scheduled_waves": [142],
                    "observed_wave": 184,
                    "observed_wave_end": 184,
                    "selection_model": "interval_aggregate",
                    "boundary_coverage": "incomplete_visibility_gap",
                    "selections": [
                        {"display_text": "Defense percent +5.00%"},
                        {"display_text": "x1.15 all coins bonuses"},
                    ],
                }
            ],
            "warnings": [],
            "pending_scheduled_wave": None,
        }
    )

    rendered = "\n".join(lines)
    assert "from 142; intermediate boundaries unobserved" in rendered
    assert "UI or process visibility gaps" in rendered


def test_render_perk_selection_timeline_marks_reconstructed_post_pwr_rows():
    lines = render_perk_selection_timeline_markdown(
        {
            "baseline_status": "new_battle_empty",
            "pwr_maxed_observed": True,
            "batches": [
                {
                    "sequence": 1,
                    "scheduled_wave": 760,
                    "scheduled_waves": [760],
                    "observed_wave": 780,
                    "observed_wave_end": 880,
                    "selection_model": (
                        "singleton_after_pwr_max_reconstructed"
                    ),
                    "selections": [
                        {"display_text": "Orbs +2"},
                    ],
                },
                {
                    "sequence": 2,
                    "scheduled_wave": 855,
                    "scheduled_waves": [855],
                    "observed_wave": 780,
                    "observed_wave_end": 880,
                    "selection_model": (
                        "singleton_after_pwr_max_reconstructed"
                    ),
                    "selections": [
                        {"display_text": "x1.44 all coins bonuses"},
                    ],
                },
            ],
            "warnings": [],
            "pending_scheduled_wave": None,
        }
    )

    rendered = "\n".join(lines)
    assert "| 1 | 760 | 780–880 | Orbs +2 |" in rendered
    assert "| 2 | 855 | 780–880 | x1.44 all coins bonuses |" in rendered
    assert "newest-first order to reconstruct" in rendered


def test_no_strategy_record_retains_terminal_tier_without_guessing_type():
    record = build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4), _frame(5)],
        source_complete=True,
        source_reason="edge_reached",
        battle_id="Battle20260715T120000-0700",
        captured_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        strategy_name="none",
        run_configuration={},
        runtime_context={"terminal_state": "GAME_OVER"},
        data_fn=_data_fn,
        game_stats_text_fn=_game_text,
    )

    assert record["battle_type"] == "unknown"
    assert record["runtime"]["observed_tier"] == 19
    assert record["battle_type_analysis"]["observed_tier"] == 19
    assert "Observed tier: 19" in render_battle_markdown(record)


def test_no_strategy_record_keeps_observed_configuration_distinct_from_intent():
    observed = {
        "schema_version": 1,
        "collection_mode": "no_strategy_observation",
        "coverage": {"observed": 2, "total": 14, "complete": False},
        "fields": {
            "run_identity": {
                "status": "observed",
                "value": {
                    "family": "Dissonance",
                    "subtype": "Attack",
                    "label": "Attack Dissonance",
                },
                "source": "tier_attack_dissonance_badge",
                "phase": "in_battle",
                "confidence": "high",
                "observed_at": "2026-07-22T16:00:00-07:00",
            },
            "free_upgrade_locks": {
                "status": "observed",
                "value": {
                    "locks": [
                        {"label": "Shockwave Size", "state": "checked"},
                    ]
                },
                "source": "home_workshop_lock_details",
                "phase": "post_run_home",
                "confidence": "high",
                "observed_at": "2026-07-22T20:00:00-07:00",
            },
        },
    }
    record = build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4), _frame(5)],
        source_complete=True,
        source_reason="edge_reached",
        battle_id="Battle20260715T120000-0700",
        captured_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        strategy_name="none",
        run_configuration={},
        runtime_context={
            "terminal_state": "GAME_OVER",
            "observed_run_configuration": observed,
        },
        data_fn=_data_fn,
        game_stats_text_fn=_game_text,
    )

    assert record["run_configuration"] == {}
    assert record["observed_run_configuration"] == observed
    assert "observed_run_configuration" not in record["runtime"]
    assert record["battle_type"] == "dissonance"
    markdown = render_battle_markdown(record)
    assert "Battle type: Dissonance (high confidence)" in markdown
    assert "Run identity: Attack Dissonance" in markdown
    assert "Free Upgrade locks: Shockwave Size=checked" in markdown
    assert "post run home; 2026-07-22T20:00:00-07:00" in markdown


def test_empty_observed_configuration_is_omitted_from_completed_record():
    record = build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4), _frame(5)],
        source_complete=True,
        source_reason="edge_reached",
        battle_id="Battle20260806T120000-0700",
        captured_at=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        strategy_name="farm_t19",
        run_configuration={"profile": "farm", "tier": 19},
        runtime_context={
            "terminal_state": "GAME_OVER",
            "observed_run_configuration": {},
        },
        data_fn=_data_fn,
        game_stats_text_fn=_game_text,
    )

    assert "observed_run_configuration" not in record
    assert "observed_run_configuration" not in record["runtime"]


def test_post_run_observations_refresh_previously_unknown_classification():
    record = {
        "battle_id": "Battle20260722T200000-0700",
        "strategy": "none",
        "battle_type": "unknown",
        "battle_type_analysis": {"type": "unknown", "confidence": "low"},
        "run_configuration": {},
        "runtime": {"terminal_state": "GAME_OVER", "observed_tier": 18},
    }
    observed = {
        "fields": {
            "run_identity": {
                "status": "observed",
                "value": {
                    "family": "Dissonance",
                    "subtype": "Attack",
                    "label": "Attack Dissonance",
                },
            }
        }
    }

    attach_observed_run_configuration(record, observed)

    assert record["battle_type"] == "dissonance"
    assert record["battle_type_analysis"]["label"] == "Attack Dissonance"


def test_utility_badge_observation_refreshes_record_with_utility_label():
    record = {
        "battle_id": "Battle20260806T190000-0700",
        "strategy": "none",
        "battle_type": "unknown",
        "battle_type_analysis": {"type": "unknown", "confidence": "low"},
        "run_configuration": {},
        "runtime": {"terminal_state": "GAME_OVER", "observed_tier": 19},
    }
    observed = {
        "fields": {
            "run_identity": {
                "status": "observed",
                "value": {
                    "family": "Dissonance",
                    "subtype": "Utility",
                    "label": "Utility Dissonance",
                },
            }
        }
    }

    attach_observed_run_configuration(record, observed)

    assert record["battle_type"] == "dissonance"
    assert record["battle_type_analysis"]["label"] == "Utility Dissonance"
    assert "observed_identity:utility_dissonance" in (
        record["battle_type_analysis"]["signals"]
    )


def test_tower_number_parser_preserves_case_sensitive_magnitudes():
    assert parse_tower_number("2q") == Decimal("2e15")
    assert parse_tower_number("2Q") == Decimal("2e18")
    assert parse_tower_number("3D") == Decimal("3e33")
    assert parse_tower_number("4aa") == Decimal("4e36")
    assert parse_tower_number("5ab") == Decimal("5e39")
    assert parse_tower_number("4.81laa") is None
    assert format_tower_number(Decimal("4.81e36")) == "4.81aa"
    assert format_tower_number(Decimal("2.5e39")) == "2.5ab"
    assert parse_duration_seconds("5h 43m 19s") == 20599
    assert parse_duration_seconds("1d 16h 10m 28s") == 144628


def test_overlapping_pages_are_sectioned_deduplicated_and_derived():
    record = _record()
    sections = {section["key"]: section for section in record["more_stats"]["sections"]}

    damage = {row["key"]: row for row in sections["damage"]["rows"]}
    currencies = {row["key"]: row for row in sections["currencies"]["rows"]}
    destroyed = {row["key"]: row for row in sections["enemies_destroyed_by"]["rows"]}
    effects = {
        row["key"]: row
        for row in sections["killed_with_effect_active"]["rows"]
    }
    assert damage["damage_dealt"]["value_raw"] == "17.04ab"
    assert damage["projectiles"]["value_raw"] == "30.14aa"
    assert damage["smart_missiles"]["value_normalized"] == "4.81aa"
    assert damage["smart_missiles"]["value_decimal"] == "4.810000000000000000000000000E+36"
    battle_report = {
        row["key"]: row for row in sections["battle_report"]["rows"]
    }
    assert battle_report["battle_date"]["value_type"] == "datetime_text"
    assert currencies["reroll_shards_earned"]["value_decimal"] == "72000.00"
    assert currencies["reroll_shards_fetched"]["value"] == 0
    assert currencies["future_stat_added_by_the_game"]["value"] == 7
    assert effects["golden_tower"]["value"] == 160698
    assert effects["golden_tower"]["active_percent"] == 100.0
    assert destroyed["projectiles"]["value"] == 40

    assert record["quality"]["valid"]
    assert not record["quality"]["retain_source_images"]
    assert record["derived"] == {
        "effective_game_speed": 5.0,
        "waves_per_real_hour": 2000.0,
        "real_seconds_per_wave": 1.8,
        "coins_per_wave_decimal": "1000000000000.00",
        "cells_per_wave_decimal": "50.00",
        "currency_rates_per_real_hour": {
            "gems": {"label": "Gems", "source_raw": "36", "value_decimal": "36"},
            "ad_gems": {"label": "Ad Gems", "source_raw": "24", "value_decimal": "24"},
            "gem_blocks_tapped": {
                "label": "Gem Blocks Tapped",
                "source_raw": "2",
                "value_decimal": "2",
            },
            "fetch_gems": {"label": "Fetch Gems", "source_raw": "3", "value_decimal": "3"},
            "medals": {"label": "Medals", "source_raw": "1", "value_decimal": "1"},
            "reroll_shards_earned": {
                "label": "Reroll Shards Earned",
                "source_raw": "72.00K",
                "value_decimal": "72000.00",
            },
            "reroll_shards_fetched": {
                "label": "Reroll Shards Fetched",
                "source_raw": "0)",
                "value_decimal": "0",
            },
            "cannon_shards": {
                "label": "Cannon Shards",
                "source_raw": "10",
                "value_decimal": "10",
            },
            "armor_shards": {
                "label": "Armor Shards",
                "source_raw": "20",
                "value_decimal": "20",
            },
            "generator_shards": {
                "label": "Generator Shards",
                "source_raw": "30",
                "value_decimal": "30",
            },
            "core_shards": {
                "label": "Core Shards",
                "source_raw": "40",
                "value_decimal": "40",
            },
            "common_modules": {
                "label": "Common Modules",
                "source_raw": "4",
                "value_decimal": "4",
            },
            "rare_modules": {
                "label": "Rare Modules",
                "source_raw": "2",
                "value_decimal": "2",
            },
            "future_stat_added_by_the_game": {
                "label": "Future Stat Added By The Game",
                "source_raw": "7",
                "value_decimal": "7",
            },
        },
        "reroll_dice_per_real_hour_decimal": "72000.00",
        "module_shards_per_real_hour_decimal": "100",
        "base_coin_share_percent": 50.0,
        "ad_coin_share_percent": 50.0,
        "death_defies": 12,
        "estimated_started_at": "2026-07-15T11:00:00+00:00",
        "runtime_wave_error": -1958,
    }


def test_incomplete_capture_is_persisted_but_retains_source_evidence():
    record = _record(source_complete=False, source_reason="source_screen_lost")

    assert not record["quality"]["valid"]
    assert record["quality"]["retain_source_images"]
    assert "source_screen_lost" in record["quality"]["warnings"][0]


def test_missing_game_stats_only_field_retains_source_evidence():
    def incomplete_game_text(_frame, *, psm):
        assert psm == 6
        return "GAME STATS Wave 2000 Tier 19 Killed By Scatter", 95.0

    record = build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4), _frame(5)],
        source_complete=True,
        source_reason="edge_reached",
        data_fn=_data_fn,
        game_stats_text_fn=incomplete_game_text,
    )

    assert not record["quality"]["valid"]
    assert record["quality"]["retain_source_images"]
    assert record["game_stats"]["quality"]["missing_required_fields"]


def test_missing_current_more_stats_sections_retain_source_evidence():
    record = build_battle_record(
        _frame(9),
        [_frame(1), _frame(2), _frame(3), _frame(4)],
        source_complete=True,
        source_reason="edge_reached",
        data_fn=_data_fn,
        game_stats_text_fn=_game_text,
    )

    assert not record["quality"]["valid"]
    assert record["quality"]["retain_source_images"]
    assert "cash" in record["more_stats"]["quality"]["missing_required_sections"]


def test_record_persists_json_markdown_and_supports_previous_wave_lookup(tmp_path):
    record = _record()
    json_path, markdown_path = persist_battle_record(record, records_dir=tmp_path)

    assert json_path.exists()
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Battle Report" in markdown
    assert "Coins Per Wave: 1T" in markdown
    assert "Cells Per Wave: 50" in markdown
    assert "Reroll Dice/hour: 72K" in markdown
    assert "Shards/hour (total module shards): 100" in markdown
    assert "### Currency rates" in markdown
    assert "Gems/hour: 36" in markdown
    assert "Common Modules/hour: 4" in markdown
    assert "Cells Earned/hour" not in markdown
    assert (
        get_previous_run_wave(records_dir=str(tmp_path))
        == 2000
    )


def test_profile_progression_is_top_level_and_persistence_adds_prior_run_delta(
    tmp_path,
):
    first = _record(profile_progression=_progression(1, menu_unlocked=False))
    persist_battle_record(first, records_dir=tmp_path)

    assert first["profile_progression"]["identity"]["save_revision"] == 1
    assert "profile_progression" not in first["runtime"]
    assert first["profile_progression_delta"]["status"] == "baseline"

    partial = _record(
        profile_progression=_progression(2, menu_unlocked=False)
    )
    partial["battle_id"] = "Battle20260715T123000-0700"
    partial["captured_at"] = "2026-07-15T12:30:00-07:00"
    partial["profile_progression"]["status"] = "partial"
    partial["profile_progression"]["complete"] = False
    persist_battle_record(partial, records_dir=tmp_path)
    assert partial["profile_progression_delta"]["status"] == "unavailable"

    second = _record(profile_progression=_progression(2, menu_unlocked=True))
    second["battle_id"] = "Battle20260715T130000-0700"
    second["captured_at"] = "2026-07-15T13:00:00-07:00"
    json_path, markdown_path = persist_battle_record(
        second,
        records_dir=tmp_path,
    )

    stored = json.loads(json_path.read_text(encoding="utf-8"))
    delta = stored["profile_progression_delta"]
    assert delta["status"] == "changed"
    assert delta["baseline_record"]["record_id"] == (
        "Battle20260715T120000-0700"
    )
    assert delta["changes"] == [
        {
            "path": "themes.menu_unlocked[1]",
            "before": False,
            "after": True,
        }
    ]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Profile Progression" in markdown
    assert "Menu 2/2" in markdown
    assert "`themes.menu_unlocked[1]`: false → true" in markdown


def _clipboard_game_text(_frame, *, psm):
    assert psm == 6
    return (
        "GAME STATS Wave 2558 Tier 19 Highest Wave: 5575 "
        "Killed By Scatter Death defied 12 times coins earned ad coins earned "
        "total coins 600.00q + 272.38q = 872.38q",
        95.0,
    )


def test_exact_clipboard_report_retains_every_section_and_row():
    text = CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8")

    stats = parse_more_stats_clipboard(text)
    sections = {section["key"]: section for section in stats["sections"]}
    rows = {
        (section["key"], row["key"]): row
        for section in stats["sections"]
        for row in section["rows"]
    }

    assert stats["source_method"] == "android_clipboard"
    assert stats["quality"]["valid"]
    assert stats["quality"]["row_count"] == 145
    assert len(sections) == 16
    assert rows[("damage", "smart_missiles")]["value_raw"] == "4.81aa"
    assert rows[("damage_taken", "tower")]["value_raw"] == "434.09Q"
    assert rows[("killed_with_effect_active", "golden_tower")]["value"] == 160698
    assert rows[("killed_with_effect_active", "golden_tower")]["active_percent"] == 100.0
    assert rows[("currencies", "reroll_shards_earned")]["value_decimal"] == "30240.00"
    assert all(row["source"] == "android_clipboard" for row in rows.values())


def test_tournament_plus_tier_is_a_valid_structured_value():
    text = CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8").replace(
        "Tier\t19",
        "Tier\t17+",
    )

    stats = parse_more_stats_clipboard(text)
    tier = next(
        row
        for section in stats["sections"]
        if section["key"] == "battle_report"
        for row in section["rows"]
        if row["key"] == "tier"
    )

    assert stats["quality"]["valid"]
    assert tier["value_type"] == "minimum_integer"
    assert tier["value"] == 17


def test_clipboard_record_is_identity_checked_and_drives_existing_derivations():
    text = CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8")
    record = build_battle_record_from_clipboard(
        _frame(9),
        text,
        battle_id="BattleClipboard",
        captured_at=datetime(2026, 7, 15, 7, 56, tzinfo=timezone.utc),
        strategy_name="gc_farm_t19_experiment",
        runtime_context={"last_wave": 2558},
        game_stats_text_fn=_clipboard_game_text,
    )

    assert record["quality"]["valid"]
    assert record["quality"]["identity"] == {
        "checked_fields": ["wave", "tier", "killed_by"],
        "mismatches": [],
        "valid": True,
    }
    assert len(record["derived"]["currency_rates_per_real_hour"]) == 13
    assert "cells_earned" not in record["derived"]["currency_rates_per_real_hour"]
    assert record["derived"]["death_defies"] == 12
    assert record["derived"]["reroll_dice_per_real_hour_decimal"]
    assert record["derived"]["module_shards_per_real_hour_decimal"]


def test_bound_player_save_report_drives_existing_record_and_derivations(tmp_path):
    record = build_battle_record_from_player_save(
        _frame(9),
        _terminal_save_report(),
        battle_id="BattlePlayerSave",
        captured_at=datetime(2026, 8, 6, 4, 0, tzinfo=timezone.utc),
        strategy_name="farm_t19",
        runtime_context={"last_wave": 2558},
        game_stats_text_fn=_clipboard_game_text,
    )

    assert record["schema_version"] == 6
    assert record["quality"]["valid"]
    assert record["quality"]["identity"] == {
        "checked_fields": ["wave", "tier", "killed_by"],
        "mismatches": [],
        "valid": True,
    }
    stats = record["more_stats"]
    assert stats["source_method"] == "player_save_battle_history"
    assert stats["quality"]["row_count"] == 144
    assert stats["source"]["history_transition"] == "capacity_rollover"
    rows = {
        (section["key"], row["key"]): row
        for section in stats["sections"]
        for row in section["rows"]
    }
    assert rows[("battle_report", "game_time")]["value_raw"] == "5h 43m 19s"
    assert rows[("battle_report", "coins_earned")]["value_raw"] == "872.38q"
    assert rows[("killed_with_effect_active", "golden_tower")][
        "active_percent"
    ] == 0.000622
    assert record["derived"]["currency_rates_per_real_hour"]
    assert "Player save Battle History" in render_battle_markdown(record)
    json_path, markdown_path = persist_battle_record(record, records_dir=tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["quality"]["valid"]
    assert "exact player save" in markdown_path.read_text(encoding="utf-8")


def test_player_save_report_keeps_compact_game_stats_as_optional_augmentation():
    record = build_battle_record_from_player_save(
        _frame(9),
        _terminal_save_report(),
        game_stats_text_fn=lambda _frame, *, psm: ("", 0.0),
    )

    assert record["quality"]["valid"]
    assert not record["game_stats"]["quality"]["augmentation_complete"]
    assert not record["game_stats"]["quality"]["required_for_record"]
    assert any(
        warning.startswith("Optional Game Stats fields unavailable")
        for warning in record["quality"]["warnings"]
    )


def test_manual_surrender_minimal_record_uses_save_only_and_excludes_analytics():
    report = _terminal_save_report()
    report["completed_entry"]["identity"]["killed_by"] = "Surrender"
    for section in report["completed_entry"]["more_stats"]["sections"]:
        for row in section["rows"]:
            if row["key"] == "killed_by":
                row["value"] = "Surrender"
                row["enum_id"] = 99

    record = build_minimal_battle_record_from_player_save(
        report,
        battle_id="BattleManualSurrender",
        runtime_context={"terminal_state": "GAME_OVER"},
    )

    assert record["game_stats"]["raw_text"] == ""
    assert record["more_stats"]["source_method"] == (
        "player_save_battle_history"
    )
    assert record["report_disposition"] == {
        "schema_version": 1,
        "outcome": "surrendered",
        "initiator": "operator_manual_control",
        "collection": "minimal_save_backed",
        "representative": False,
        "analytics": "excluded",
        "history": "excluded_by_default",
        "reason": "manual surrender confirmed before optional UI enrichment",
        "provenance": {
            "mapping_id": "data-9-game-1073",
            "capture": report["capture"],
            "run_binding": {},
            "history_transition": report["history_transition"],
        },
    }


def test_nonrepresentative_surrender_record_is_idempotent_and_collision_safe(
    tmp_path,
):
    report = _terminal_save_report()
    report["completed_entry"]["identity"]["killed_by"] = "Surrender"
    record = build_minimal_battle_record_from_player_save(
        report,
        battle_id="BattleManualSurrender",
    )

    json_path, markdown_path = persist_battle_record(
        record,
        records_dir=tmp_path,
    )
    markdown_path.unlink()
    repeated_json, repeated_markdown = persist_battle_record(
        record,
        records_dir=tmp_path,
    )

    assert repeated_json == json_path
    assert repeated_markdown == markdown_path
    assert repeated_markdown.exists()
    assert included_in_default_history(record) is False

    collision = dict(record)
    collision["report_disposition"] = {
        **record["report_disposition"],
        "collection": "full_terminal_ui",
    }
    with pytest.raises(FileExistsError, match="non-representative"):
        persist_battle_record(collision, records_dir=tmp_path)


def test_previous_wave_ignores_newer_nonrepresentative_surrender(tmp_path):
    representative = _record()
    persist_battle_record(representative, records_dir=tmp_path)
    surrender = _record()
    surrender["battle_id"] = "Battle20260715T130000-0700"
    surrender["captured_at"] = "2026-07-15T13:00:00-07:00"
    surrender["more_stats"]["sections"][0]["rows"][0]["value"] = 9999
    surrender["report_disposition"] = {
        "schema_version": 1,
        "outcome": "surrendered",
        "representative": False,
        "analytics": "excluded",
        "history": "excluded_by_default",
    }
    persist_battle_record(surrender, records_dir=tmp_path)

    assert get_previous_run_wave(records_dir=str(tmp_path)) == 2000


def test_unbound_terminal_record_is_valid_but_warns_about_omitted_run_evidence():
    record = build_battle_record_from_clipboard(
        _frame(9),
        CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8"),
        strategy_name=None,
        run_configuration={},
        runtime_context={
            "terminal_state": "GAME_OVER",
            "run_binding": {
                "schema_version": 1,
                "status": "unbound",
                "reason": "terminal_without_observed_active_battle",
                "activity_scope_run_id": "stale-scope",
                "observed_active_scope_run_id": None,
            },
        },
        game_stats_text_fn=_clipboard_game_text,
    )

    assert record["quality"]["valid"]
    assert record["battle_type"] == "unknown"
    assert record["strategy"] is None
    assert record["run_configuration"] == {}
    assert record["runtime"]["run_binding"]["status"] == "unbound"
    warning = record["quality"]["warnings"][-1]
    assert "Process-local run evidence was omitted" in warning
    assert warning in render_battle_markdown(record)


def test_live_clipboard_report_may_omit_historical_battle_date():
    text = CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8").replace(
        "Battle Date\tJul 15, 2026 06:46\n",
        "",
    )

    stats = parse_more_stats_clipboard(text)

    assert stats["quality"]["valid"]
    assert stats["quality"]["row_count"] == 144


def test_clipboard_record_rejects_stale_battle_identity():
    def mismatched_game_text(_frame, *, psm):
        text, confidence = _clipboard_game_text(_frame, psm=psm)
        return text.replace("Wave 2558", "Wave 4969"), confidence

    record = build_battle_record_from_clipboard(
        _frame(9),
        CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8"),
        game_stats_text_fn=mismatched_game_text,
    )

    assert not record["quality"]["valid"]
    assert record["quality"]["identity"]["mismatches"] == [
        {"field": "wave", "game_stats": 4969, "more_stats": 2558}
    ]


def test_perk_order_and_instance_model_are_rendered_for_perusal():
    record = _record()
    perks = {
        "order_semantics": "latest_selected_first",
        "selected": [
            {
                "latest_selection_rank": 1,
                "color": "purple",
                "instance_model": "single_instance",
                "display_text": "Boss health -73.5%, but boss speed +50%",
                "confidence": 94.0,
            },
            {
                "latest_selection_rank": 2,
                "color": "blue",
                "instance_model": "leveled",
                "display_text": "Defense percent +25.00%",
                "confidence": 93.0,
            },
        ],
        "quality": {"valid": True, "warnings": []},
    }

    attach_battle_perks(record, perks)
    markdown = render_battle_markdown(record)

    assert "## Selected Perks" in markdown
    assert "latest selection first" in markdown
    assert "| 1 | purple | single_instance | Boss health" in markdown
    assert "| 2 | blue | leveled | Defense percent +25.00%" in markdown


def test_exact_save_backed_perk_prefix_renders_without_ocr_row_shape():
    record = _record()
    perks = {
        "source_method": "player_save_perk_checkpoint",
        "exact_saved_prefix": {
            "save_revision": 42,
            "saved_wave": 700,
            "captured_at": "2026-08-07T12:00:00+00:00",
        },
        "exact_saved_picks": [
            {
                "sequence": 1,
                "saved_wave": 100,
                "perk_key": "max_health",
                "level_after": 1,
            }
        ],
        "terminal_tail": {"status": "not_required", "aggregates": []},
        "quality": {"valid": True, "warnings": []},
    }

    attach_battle_perks(record, perks)
    markdown = render_battle_markdown(record)

    assert "exact oldest-first selection order" in markdown
    assert "| 1 | 100 | Max Health | 1 | exact saved pick |" in markdown
    assert "No terminal aggregate was needed" in markdown


def test_compact_coin_suffix_ocr_is_reconciled_against_exact_copied_total():
    def coin_icon_game_text(_frame, *, psm):
        assert psm == 6
        return (
            "GAME STATS Wave 2558 Tier 19 Highest Wave: 5575 "
            "Killed By Scatter Death defied 12 times coins earned ad coins earned "
            "total coins 3000© + 1500© = 4.490©",
            92.4,
        )

    report = CLIPBOARD_REPORT_PATH.read_text(encoding="utf-8").replace(
        "872.38q",
        "4.49Q",
    )
    record = build_battle_record_from_clipboard(
        _frame(9),
        report,
        game_stats_text_fn=coin_icon_game_text,
    )
    fields = record["game_stats"]["fields"]

    assert record["quality"]["valid"]
    assert record["game_stats"]["quality"]["coin_breakdown"] == {
        "valid": True,
        "reconciled": True,
        "copied_total_raw": "4.49Q",
        "suffix": "Q",
        "warnings": [],
    }
    assert fields["base_coins_earned"]["raw"] == "3.00Q"
    assert fields["ad_coins_earned"]["raw"] == "1.50Q"
    assert fields["total_coins_earned"]["raw"] == "4.49Q"
    assert record["derived"]["base_coin_share_percent"] == 66.815
    assert record["derived"]["ad_coin_share_percent"] == 33.408
