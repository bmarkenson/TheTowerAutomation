from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch

import cv2

from core.matcher import get_match
from core.state_detector import detect_state_and_overlays
from core.tournament_results import (
    build_tournament_result,
    find_recent_tournament_result,
    ocr_tournament_summary,
    persist_tournament_result,
)
from handlers.tournament_result_handler import handle_tournament_results


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"


def _load(name: str):
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None, name
    return image


def test_tournament_summary_is_a_distinct_terminal_state():
    tournament = _load("tournament_stats_20260718.png")
    normal = _load("game_over_stats_20260715.png")

    assert detect_state_and_overlays(tournament)["state"] == "TOURNAMENT_RESULTS"
    assert detect_state_and_overlays(normal)["state"] == "GAME_OVER"
    point, confidence = get_match(
        "buttons.more_stats:tournament",
        screenshot=tournament,
    )
    assert point == (542, 938)
    assert confidence >= 0.99


def test_tournament_summary_ocr_tracks_rank_and_coin_split():
    summary = ocr_tournament_summary(_load("tournament_stats_20260718.png"))

    assert summary["quality"]["valid"]
    assert summary["fields"]["league"]["value"] == "Legend League"
    assert summary["fields"]["wave"]["value"] == 2028
    assert summary["fields"]["killed_by"]["value"] == "Boss"
    assert summary["fields"]["rank"]["value"] == 4
    assert summary["fields"]["coins_earned"]["raw"] == "5.96T"
    assert summary["fields"]["ad_coins_earned"]["raw"] == "2.98T"


def test_tournament_result_persists_summary_and_exact_detailed_report(tmp_path):
    report = (FIXTURES / "battle_report_clipboard.txt").read_text(encoding="utf-8")
    frame = _load("tournament_stats_20260718.png")

    def summary_text(_crop, *, psm):
        assert psm == 6
        return (
            "TOURNAMENT STATS Legend League Wave 2558 Killed By Scatter "
            "currently at rank: 4 coins earned ad coins earned 5.96T © 2.98T ©",
            99.0,
        )

    record = build_tournament_result(
        frame,
        report,
        captured_at=datetime.fromisoformat("2026-07-18T06:20:00-07:00"),
        strategy_name="tournament",
        run_configuration={"profile": "tournament", "tier": "17+"},
        runtime_context={
            "terminal_state": "TOURNAMENT_RESULTS",
            "coin_rate_samples": [
                {
                    "captured_at": "2026-07-18T05:00:00-07:00",
                    "wave": 1500,
                    "display": "2.50T",
                    "confidence": 97.0,
                }
            ],
            "survival_ability_activations": {
                "schema_version": 4,
                "source": "visual_transition_detection",
                "second_wind_activations": [
                    {
                        "ability": "second_wind",
                        "sequence": 1,
                        "approximate_wave": 2100,
                        "estimated_rearm_wave": 2500,
                        "detected_at": "2026-07-18T05:30:00-07:00",
                    }
                ],
                "demon_mode_first_activation": {
                    "ability": "demon_mode",
                    "sequence": 1,
                    "approximate_wave": 2120,
                    "detected_at": "2026-07-18T05:30:30-07:00",
                },
                "nuke_activations": [],
            },
        },
        summary_text_fn=summary_text,
    )
    json_path, markdown_path = persist_tournament_result(
        record,
        records_dir=tmp_path,
    )

    assert record["quality"]["valid"]
    assert record["battle_type"] == "tournament"
    assert record["runtime"]["observed_tier"] == 19
    assert record["battle_type_analysis"]["observed_tier"] == 19
    assert record["quality"]["identity"] == {
        "summary_wave": 2558,
        "detailed_wave": 2558,
        "checked": True,
        "mismatch": False,
    }
    assert json_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Rank at completion: 4" in markdown
    assert "Observed tier: 19" in markdown
    assert "## Battle Report" in markdown
    assert "| Wave | 2558 |" in markdown
    assert "## Coins/min progression" in markdown
    assert "| 2026-07-18T05:00:00-07:00 | 1500 | 2.50T | 97.0% |" in markdown
    assert "## Survival ability activations" in markdown
    assert "| 1 | 2100 | 2500 | 2026-07-18T05:30:00-07:00 |" in markdown
    assert "Demon Mode first activation: approximately wave 2120" in markdown

    matched = find_recent_tournament_result(
        frame,
        records_dir=tmp_path,
        now=datetime.fromisoformat("2026-07-18T06:30:00-07:00"),
    )
    assert matched is None

    matching_record = build_tournament_result(
        frame,
        captured_at=datetime.fromisoformat("2026-07-18T06:25:00-07:00"),
    )
    matching_record["quality"]["valid"] = True
    persist_tournament_result(matching_record, records_dir=tmp_path)
    matched = find_recent_tournament_result(
        frame,
        records_dir=tmp_path,
        now=datetime.fromisoformat("2026-07-18T06:30:00-07:00"),
    )
    assert matched is not None
    assert matched["tournament_id"] == matching_record["tournament_id"]


def test_tournament_handler_uses_only_visible_detail_controls_and_never_ok():
    summary = _load("tournament_stats_20260718.png")
    detailed = _load("tournament_more_stats_bottom_20260718.png")
    report = (FIXTURES / "battle_report_clipboard.txt").read_text(encoding="utf-8")

    def visible(label, *, screenshot):
        if label == "indicators.tournament_stats":
            return screenshot is summary
        if label == "indicators.more_stats":
            return screenshot is detailed
        return False

    with (
        patch(
            "handlers.tournament_result_handler.capture_adb_screenshot",
            side_effect=[detailed, detailed, summary],
        ),
        patch(
            "handlers.tournament_result_handler.find_recent_tournament_result",
            return_value=None,
        ),
        patch(
            "handlers.tournament_result_handler.is_visible",
            side_effect=visible,
        ),
        patch(
            "handlers.tournament_result_handler.tap_if_visible",
            return_value=True,
        ) as tap,
        patch(
            "handlers.tournament_result_handler._copy_detailed_report",
            return_value=(report, "clipboard_copy"),
        ),
        patch(
            "handlers.tournament_result_handler.persist_tournament_result",
            return_value=(Path("result.json"), Path("result.md")),
        ),
        patch("handlers.tournament_result_handler._retain_evidence"),
        patch("handlers.tournament_result_handler.time.sleep"),
    ):
        record = handle_tournament_results(summary)

    assert record is not None
    assert [item.args[0] for item in tap.call_args_list] == [
        "buttons.more_stats:tournament",
        "buttons.close:more_stats",
    ]
    assert tap.call_args_list == [
        call("buttons.more_stats:tournament", screenshot=summary, retries=1),
        call("buttons.close:more_stats", screenshot=detailed, retries=1),
    ]
    assert not any("ok" in item.args[0].lower() for item in tap.call_args_list)


def test_round_stats_copy_control_is_matched_not_static():
    detailed = _load("tournament_more_stats_bottom_20260718.png")

    point, confidence = get_match("buttons.copy:more_stats", screenshot=detailed)

    assert point == (907, 1634)
    assert confidence >= 0.99
