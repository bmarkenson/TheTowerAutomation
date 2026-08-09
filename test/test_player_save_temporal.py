from types import SimpleNamespace

from core.player_save_temporal import (
    BoundRunningAttachmentSaveEvidence,
    PlayerSaveTemporalClass,
)
from test.player_save_temporal_fixtures import (
    running_attachment_observations,
)


def _context(**changes):
    values = {
        "runtime_session_id": "runtime-1",
        "activity_scope_id": "scope-1",
        "target": "private-target",
        "target_generation": 3,
        "active_battle_observed": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_attachment_facts_have_separate_temporal_classes():
    observations = running_attachment_observations(
        {
            "workshop_preset": "Tourney",
            "free_upgrade_locks": ["Shockwave Size"],
            "guardian_chips": ["Fetch", "Scout"],
            "bots_preset": "Farm",
            "modules": {"cannon_primary": "Amplifying Strike"},
            "perk_bans": ["interest"],
            "perk_first_choice": "perk_wave_requirement",
            "perk_auto_pick_order": ["game_speed", "damage"],
            "cards_deck": "Farm",
            "bots_progression": {"medals_spent": 42},
        }
    )

    classes = {
        fact.check_id: fact.temporal_class for fact in observations.facts
    }
    assert classes == {
        "workshop_preset": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "free_upgrade_locks": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "guardian_chips": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "bots_preset": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "modules": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "perk_bans": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "perk_first_choice": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "perk_auto_pick_order": PlayerSaveTemporalClass.ROUND_INVARIANT,
        "cards_deck": PlayerSaveTemporalClass.POINT_IN_TIME,
        "bots_progression": PlayerSaveTemporalClass.CURRENT_CONFIGURATION,
    }


def test_bound_consumer_accepts_every_exact_bound_class_once():
    observations = running_attachment_observations(
        {
            "workshop_preset": "Tourney",
            "cards_deck": "Farm",
        }
    )
    evidence = BoundRunningAttachmentSaveEvidence(observations, _context)

    assert evidence.temporal_class("cards_deck") is (
        PlayerSaveTemporalClass.POINT_IN_TIME
    )
    assert not evidence.mismatch_is_report_only("cards_deck")
    assert evidence.consume("cards_deck") == "Farm"
    assert evidence.consume("cards_deck") is None
    assert evidence.mismatch_is_report_only("workshop_preset")
    assert evidence.consume("workshop_preset") == "Tourney"
    assert evidence.consume("workshop_preset") is None


def test_bound_consumer_rejects_target_scope_and_process_changes():
    observations = running_attachment_observations(
        {"workshop_preset": "Tourney"}
    )
    contexts = iter(
        (
            _context(target_generation=4),
            _context(),
        )
    )
    evidence = BoundRunningAttachmentSaveEvidence(
        observations,
        lambda: next(contexts),
    )

    assert evidence.consume("workshop_preset") is None
    # A failed exact-binding check invalidates the one-use carrier permanently.
    assert evidence.consume("workshop_preset") is None

    for changed in (
        _context(activity_scope_id="scope-2"),
        _context(runtime_session_id="runtime-2"),
        _context(target="other-target"),
        _context(active_battle_observed=False),
    ):
        candidate = BoundRunningAttachmentSaveEvidence(
            observations,
            lambda changed=changed: changed,
        )
        assert candidate.consume("workshop_preset") is None
