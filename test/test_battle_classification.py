from core.battle_classification import analyze_battle_type, classification_for_record


def test_farm_strategy_is_a_high_confidence_farm_game_over():
    result = analyze_battle_type(
        strategy_name="farm_t18",
        run_configuration={"profile": "farm"},
        terminal_state="GAME_OVER",
    )

    assert result["type"] == "farm"
    assert result["confidence"] == "high"


def test_distinct_tournament_terminal_wins_over_shared_configuration():
    result = analyze_battle_type(
        strategy_name="tournament",
        run_configuration={"profile": "tournament"},
        terminal_state="TOURNAMENT_RESULTS",
    )

    assert result["type"] == "tournament"
    assert "terminal_state:TOURNAMENT_RESULTS" in result["signals"]


def test_standard_game_over_separates_milestone_from_tournament_settings():
    result = analyze_battle_type(
        strategy_name="tournament",
        run_configuration={"profile": "tournament"},
        terminal_state="GAME_OVER",
    )

    assert result["type"] == "milestone"
    assert "rather than Tournament Results" in result["reason"]


def test_shared_settings_without_terminal_evidence_remain_unknown():
    result = analyze_battle_type(
        strategy_name="tournament",
        run_configuration={"profile": "tournament"},
    )

    assert result["type"] == "unknown"
    assert result["confidence"] == "low"


def test_historical_record_identity_supplies_terminal_evidence():
    result = classification_for_record(
        {
            "battle_id": "Battle20260719T101126-0700",
            "strategy": "tournament",
            "run_configuration": {"profile": "tournament"},
        }
    )

    assert result["type"] == "milestone"
    assert "record_identity:game_over" in result["signals"]
