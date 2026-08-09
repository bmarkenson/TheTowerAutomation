from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from core.module_icon_index import EquippedModuleMatch
from core.no_strategy_observer import (
    ATTACK_DISSONANCE_BADGE_REGION,
    DISSONANCE_BADGE_REGION,
    NoStrategyRunObserver,
    OBSERVED_FIELDS,
    detect_attack_dissonance_badge,
    detect_dissonance_badge,
)
from test.player_save_temporal_fixtures import (
    running_attachment_observations,
)


def _clock():
    return datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)


def _utility_dissonance_frame():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    badge = cv2.imread("test/fixtures/utility_dissonance_badge_20260806.png")
    assert badge is not None
    x, y, width, height = DISSONANCE_BADGE_REGION
    frame[y : y + height, x : x + width] = badge
    return frame


def _synthetic_dissonance_frame(subtype: str):
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    x, y, width, height = DISSONANCE_BADGE_REGION
    frame[y : y + height, x : x + width] = purple
    reference = cv2.imread(
        f"assets/match_templates/navigation/goto_{subtype}.png"
    )
    assert reference is not None
    hsv = cv2.cvtColor(reference, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array((0, 10, 80), dtype=np.uint8),
        np.array((179, 255, 255), dtype=np.uint8),
    )
    contours, _hierarchy = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    ref_x, ref_y, ref_width, ref_height = cv2.boundingRect(
        max(contours, key=cv2.contourArea)
    )
    symbol = mask[ref_y : ref_y + ref_height, ref_x : ref_x + ref_width]
    scale = min(26 / ref_width, 26 / ref_height)
    symbol = cv2.resize(
        symbol,
        (max(1, round(ref_width * scale)), max(1, round(ref_height * scale))),
        interpolation=cv2.INTER_NEAREST,
    )
    symbol_y = 1007
    symbol_x = 703 - symbol.shape[1] // 2
    target = frame[
        symbol_y : symbol_y + symbol.shape[0],
        symbol_x : symbol_x + symbol.shape[1],
    ]
    target[symbol > 0] = (255, 255, 255)
    return frame


def test_utility_dissonance_badge_is_localized_and_subtyped_from_real_crop():
    frame = _utility_dissonance_frame()

    evidence = detect_dissonance_badge(frame)

    assert evidence["observed"] is True
    assert evidence["subtype"] == "Utility"
    assert evidence["label"] == "Utility Dissonance"
    assert evidence["purple_pixels"] == 1061
    assert evidence["icon_shape_scores"]["Utility"] < 0.20
    assert detect_attack_dissonance_badge(frame)["observed"] is False


def test_attack_dissonance_requires_the_sword_icon_not_purple_alone():
    frame = _synthetic_dissonance_frame("attack")

    evidence = detect_dissonance_badge(frame)

    assert evidence["observed"] is True
    assert evidence["subtype"] == "Attack"
    assert detect_attack_dissonance_badge(frame)["observed"] is True


def test_unvalidated_defense_icon_keeps_only_dissonance_family_evidence():
    evidence = detect_dissonance_badge(_synthetic_dissonance_frame("defense"))
    scores = evidence["icon_shape_scores"]

    assert evidence["observed"] is True
    assert min(scores, key=scores.get) == "Defense"
    assert evidence["subtype"] is None


def test_purple_badge_without_a_recognized_icon_keeps_subtype_unknown():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    x, y, width, height = ATTACK_DISSONANCE_BADGE_REGION
    frame[y + 10 : y + height - 10, x + 10 : x + width - 10] = purple

    evidence = detect_dissonance_badge(frame)

    assert evidence["observed"] is True
    assert evidence["subtype"] is None
    assert evidence["label"] == "Dissonance"
    assert detect_attack_dissonance_badge(frame)["observed"] is False


def test_purple_pixels_outside_badge_do_not_invent_dissonance():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    frame[400:800, 100:500] = (255, 0, 255)

    evidence = detect_dissonance_badge(frame)

    assert evidence["observed"] is False
    assert evidence["purple_pixels"] == 0


def test_no_strategy_observer_records_utility_dissonance_without_attack_constraint():
    frame = _utility_dissonance_frame()
    observer = NoStrategyRunObserver(clock=_clock)

    observer.observe(
        frame,
        {"state": "RUNNING", "menu": "UTILITY_MENU", "secondary_states": []},
    )
    snapshot = observer.snapshot()

    identity = snapshot["fields"]["run_identity"]
    assert identity["status"] == "observed"
    assert identity["value"]["subtype"] == "Utility"
    assert identity["value"]["label"] == "Utility Dissonance"
    assert identity["source"] == "tier_utility_dissonance_badge"
    assert identity["phase"] == "in_battle"
    assert snapshot["coverage"] == {
        "observed": 1,
        "evidence_captured": 0,
        "unavailable": 0,
        "total": len(OBSERVED_FIELDS),
        "complete": False,
    }
    damage = snapshot["fields"]["damage_slider"]
    assert damage["status"] == "not_observed"


def test_weaker_generic_badge_frame_does_not_erase_known_utility_subtype():
    observer = NoStrategyRunObserver(clock=_clock)
    detection = {
        "state": "RUNNING",
        "menu": "UTILITY_MENU",
        "secondary_states": [],
    }
    observer.observe(_utility_dissonance_frame(), detection)
    generic = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(
        np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR
    )[0, 0]
    x, y, width, height = DISSONANCE_BADGE_REGION
    generic[y : y + height, x : x + width] = purple

    observer.observe(generic, detection)

    identity = observer.snapshot()["fields"]["run_identity"]
    assert identity["value"]["label"] == "Utility Dissonance"
    assert identity["source"] == "tier_utility_dissonance_badge"


def test_conflicting_later_badge_shape_cannot_replace_known_utility_subtype():
    observer = NoStrategyRunObserver(clock=_clock)
    detection = {
        "state": "RUNNING",
        "menu": "UTILITY_MENU",
        "secondary_states": [],
    }
    observer.observe(_utility_dissonance_frame(), detection)

    observer.observe(_synthetic_dissonance_frame("attack"), detection)

    snapshot = observer.snapshot()
    identity = snapshot["fields"]["run_identity"]
    assert identity["value"]["label"] == "Utility Dissonance"
    assert identity["source"] == "tier_utility_dissonance_badge"
    assert snapshot["fields"]["damage_slider"]["status"] == "not_observed"


def test_no_strategy_observer_applies_attack_only_damage_constraint():
    observer = NoStrategyRunObserver(clock=_clock)

    observer.observe(
        _synthetic_dissonance_frame("attack"),
        {"state": "RUNNING", "menu": "UTILITY_MENU", "secondary_states": []},
    )
    snapshot = observer.snapshot()

    identity = snapshot["fields"]["run_identity"]
    assert identity["value"]["label"] == "Attack Dissonance"
    damage = snapshot["fields"]["damage_slider"]
    assert damage["status"] == "unavailable"
    assert damage["source"] == "attack_dissonance_menu_constraint"


def test_authoritatively_unavailable_field_counts_as_resolved_coverage():
    observer = NoStrategyRunObserver(clock=_clock)

    observer.record_unavailable(
        "damage_slider",
        reason="Attack menu disabled by Attack Dissonance",
        source="attack_dissonance_menu_constraint",
        phase="in_battle",
    )

    snapshot = observer.snapshot()
    damage = snapshot["fields"]["damage_slider"]
    assert damage["status"] == "unavailable"
    assert damage["value"] is None
    assert damage["reason"] == "Attack menu disabled by Attack Dissonance"
    assert snapshot["coverage"]["unavailable"] == 1


def test_post_run_value_preserves_home_phase_and_observation_time():
    observer = NoStrategyRunObserver(clock=_clock)

    observer.record_post_run_value(
        "free_upgrade_locks",
        {"Shockwave Size": "checked"},
        source="home_workshop_lock_details",
        observed_at="2026-07-22T20:00:00-07:00",
    )
    snapshot = observer.snapshot(finalized=True)

    locks = snapshot["fields"]["free_upgrade_locks"]
    assert locks == {
        "status": "observed",
        "value": {"Shockwave Size": "checked"},
        "source": "home_workshop_lock_details",
        "phase": "post_run_home",
        "confidence": "high",
        "observed_at": "2026-07-22T20:00:00-07:00",
    }
    assert snapshot["finalized"] is True
    assert snapshot["finalized_at"] == "2026-07-22T16:00:00+00:00"


def test_guarded_attachment_save_records_only_normalized_observed_values():
    observer = NoStrategyRunObserver(clock=_clock)
    observations = {
        "schema_version": 1,
        "source": "guarded_active_attachment_player_save",
        "mapping_id": "data-9-game-1073",
        "mapping_maturity": "candidate",
        "captured_at": "2026-08-06T23:31:05+00:00",
        "checks": {
            "cards_deck": {"value": "Farm"},
            "workshop_preset": {"value": "Attack Disso"},
            "free_upgrade_locks": {"value": ["Shockwave Size"]},
            "bots_preset": {"value": "Farm"},
            "guardian_chips": {"value": ["Fetch", "Summon", "Scout"]},
            "modules": {"value": {"cannon_primary": "Amplifying Strike"}},
            "target_priority": {"value": ["Fast", "Boss", "Closest"]},
            "auto_pick_perks": {"value": True},
            "perk_first_choice": {"value": "perk_wave_requirement"},
            "perk_bans": {"value": ["swamp_radius"]},
            "perk_auto_pick_order": {
                "value": ["perk_wave_requirement", "game_speed"]
            },
            "ultimate_weapon_primaries": {
                "value": {
                    "Poison Swamp": {"primary": "on"},
                    "Spotlight": {"primary": "on"},
                }
            },
            "poison_swamp_stun": {"value": "off"},
            "spotlight_missiles": {"value": "on"},
        },
    }

    applied = observer.record_player_save_observations(
        running_attachment_observations(observations["checks"])
    )
    snapshot = observer.snapshot()

    assert set(applied) == set(OBSERVED_FIELDS).difference(
        {"run_identity", "damage_slider"}
    )
    assert observer.unresolved_fields() == {"run_identity", "damage_slider"}
    assert snapshot["fields"]["cards_deck"]["value"] == {"label": "Farm"}
    assert snapshot["fields"]["auto_pick_perks"]["value"] == {
        "enabled": True
    }
    ultimate = snapshot["fields"]["ultimate_weapons"]
    assert ultimate["value"]["Poison Swamp"] == {
        "primary": "on",
        "stun": "off",
    }
    assert ultimate["value"]["Spotlight"] == {
        "primary": "on",
        "missiles": "on",
    }
    assert ultimate["phase"] == "in_battle_attachment_save"
    provenance = ultimate["provenance"]
    assert provenance["mapping_id"] == "data-9-game-1073"
    assert provenance["save_checks"] == [
        "ultimate_weapon_primaries",
        "poison_swamp_stun",
        "spotlight_missiles",
    ]
    assert provenance["temporal"]["temporal_class"] == (
        "current_configuration"
    )
    assert provenance["temporal"]["target_generation"]
    assert provenance["temporal"]["activity_scope"]
    assert "private-target" not in str(provenance)
    assert "scope-1" not in str(provenance)


def test_round_invariant_save_facts_feed_actual_loadout_with_temporal_provenance():
    observer = NoStrategyRunObserver(clock=_clock)
    observations = running_attachment_observations(
        {
            "workshop_preset": "Attack Disso",
            "free_upgrade_locks": ["Shockwave Size", "Bounce Shot Range"],
            "guardian_chips": ["Fetch", "Summon", "Scout"],
            "bots_preset": "Farm",
            "modules": {"cannon_primary": "Amplifying Strike"},
            "perk_auto_pick_order": ["game_speed", "damage"],
            "cards_deck": "Farm",
            "bots_progression": {"medals_spent": 42},
        }
    )

    observer.record_player_save_observations(observations)
    fields = observer.snapshot()["fields"]

    for field in (
        "workshop_preset",
        "free_upgrade_locks",
        "guardian_chips",
        "bots_preset",
        "modules",
        "perk_auto_pick_order",
    ):
        assert fields[field]["status"] == "observed"
        assert fields[field]["provenance"]["temporal"][
            "temporal_class"
        ] == "round_invariant"
    assert fields["cards_deck"]["provenance"]["temporal"][
        "temporal_class"
    ] == "point_in_time"
    # Bot progression is deliberately not the selected Bot preset fact.
    assert fields["bots_preset"]["value"] == {"label": "Farm"}
    assert "medals_spent" not in str(fields["bots_preset"])


def test_same_round_invariant_conflict_is_sticky_and_fails_closed():
    observer = NoStrategyRunObserver(clock=_clock)
    first = running_attachment_observations(
        {"workshop_preset": "Farm"}
    )
    conflict = running_attachment_observations(
        {"workshop_preset": "Tourney"},
        captured_at="2026-08-06T23:32:05+00:00",
    )

    observer.record_player_save_observations(first)
    observer.record_player_save_observations(conflict)
    observer.record_player_save_observations(first)

    workshop = observer.snapshot()["fields"]["workshop_preset"]
    assert workshop["status"] == "unavailable"
    assert workshop["reason"] == "same_round_invariant_conflict"
    assert workshop["value"] is None


def test_cards_remain_point_in_time_and_can_change_at_a_later_boundary():
    observer = NoStrategyRunObserver(clock=_clock)
    observer.record_player_save_observations(
        running_attachment_observations({"cards_deck": "Farm"})
    )
    observer.record_player_save_observations(
        running_attachment_observations(
            {"cards_deck": "Tourney"},
            captured_at="2026-08-06T23:32:05+00:00",
        )
    )

    cards = observer.snapshot()["fields"]["cards_deck"]
    assert cards["status"] == "observed"
    assert cards["value"] == {"label": "Tourney"}
    assert cards["provenance"]["temporal"]["temporal_class"] == (
        "point_in_time"
    )
    assert cards["observed_at"] == "2026-08-06T23:32:05+00:00"


def test_changed_temporal_binding_rejects_before_any_partial_merge():
    observer = NoStrategyRunObserver(clock=_clock)
    observer.record_player_save_observations(
        running_attachment_observations({"workshop_preset": "Farm"})
    )
    changed = running_attachment_observations(
        {
            "cards_deck": "Tourney",
            "bots_preset": "Tourney",
        },
        target_generation=4,
    )

    with pytest.raises(ValueError, match="temporal binding changed"):
        observer.record_player_save_observations(changed)

    fields = observer.snapshot()["fields"]
    assert fields["cards_deck"]["status"] == "not_observed"
    assert fields["bots_preset"]["status"] == "not_observed"


def test_partial_guardian_and_module_ui_cannot_replace_save_invariants():
    observer = NoStrategyRunObserver(clock=_clock)
    observer.record_player_save_observations(
        running_attachment_observations(
            {
                "guardian_chips": ["Fetch", "Summon", "Scout"],
                "modules": {"cannon_primary": "Amplifying Strike"},
            }
        )
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    observer.observe(
        frame,
        {
            "state": "GUILD",
            "secondary_states": ["GUILD_GUARDIAN_SCREEN", "GUARDIAN_FETCH_EQUIPPED"],
        },
    )
    with patch(
        "core.no_strategy_observer.identify_equipped_ancestral_modules",
        return_value=[
            EquippedModuleMatch(
                slot_key="cannon_primary",
                family="cannon",
                role="primary",
                status="matched",
                name="Different Module",
                slug="different-module",
                confidence=0.99,
                margin=0.5,
                green_fraction=0.8,
                best_candidate="Different Module",
                runner_up="Other Module",
            )
        ],
    ):
        observer.observe(frame, {"state": "MODULES"})

    fields = observer.snapshot()["fields"]
    assert fields["guardian_chips"]["value"] == [
        "Fetch",
        "Summon",
        "Scout",
    ]
    assert fields["modules"]["value"] == {
        "cannon_primary": "Amplifying Strike"
    }


def test_authoritative_same_round_preset_ui_conflict_fails_closed():
    observer = NoStrategyRunObserver(clock=_clock)
    observer.record_player_save_observations(
        running_attachment_observations({"workshop_preset": "Farm"})
    )

    observer.record_post_run_value(
        "workshop_preset",
        {"slot": 2, "label": "Tourney"},
        source="workshop_preset_selected_border",
        confidence="high",
    )

    workshop = observer.snapshot()["fields"]["workshop_preset"]
    assert workshop["status"] == "unavailable"
    assert workshop["reason"] == "same_round_invariant_conflict"


def test_unfinished_snapshot_can_be_restored_after_process_reload():
    original = NoStrategyRunObserver(clock=_clock)
    original.record_post_run_value(
        "free_upgrade_locks",
        {"locks": [{"label": "Shockwave Size", "state": "checked"}]},
        source="home_workshop_lock_details",
    )
    snapshot = original.snapshot(finalized=False)
    replacement = NoStrategyRunObserver(
        clock=lambda: datetime(2026, 7, 22, 17, 0, tzinfo=timezone.utc)
    )

    replacement.restore_snapshot(snapshot)
    restored = replacement.snapshot(finalized=False)

    assert restored["started_at"] == snapshot["started_at"]
    assert restored["fields"] == snapshot["fields"]


def test_ultimate_weapon_observations_merge_across_visible_scroll_positions():
    observer = NoStrategyRunObserver(clock=_clock)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    first = type("Box", (), {"text": "Poison Swamp", "toggles": {"primary": "on"}})()
    second = type("Box", (), {"text": "Poison Swamp", "toggles": {"stun": "off"}})()

    with patch(
        "core.no_strategy_observer.detect_visible_boxes",
        side_effect=[{"left": [first]}, {"right": [second]}],
    ):
        detection = {"state": "RUNNING", "menu": "UW_MENU"}
        observer.observe(frame, detection)
        observer.observe(frame, detection)

    field = observer.snapshot()["fields"]["ultimate_weapons"]
    assert field["value"] == {"Poison Swamp": {"primary": "on", "stun": "off"}}


def test_selected_preset_labels_are_read_from_fixture_interiors():
    observer = NoStrategyRunObserver(clock=_clock)
    samples = (
        (
            "cards_deck",
            "test/fixtures/cards_farm_active_20260717.png",
            {"state": "CARDS"},
        ),
        (
            "workshop_preset",
            "test/fixtures/workshop_farm_active_20260714.png",
            {"state": "WORKSHOP"},
        ),
        (
            "bots_preset",
            "test/fixtures/event_bots_farm_active_20260713.png",
            {"state": "EVENT", "secondary_states": ["EVENT_BOTS_SCREEN"]},
        ),
    )

    for _field, path, detection in samples:
        frame = cv2.imread(path)
        assert frame is not None
        observer.observe(frame, detection)

    fields = observer.snapshot()["fields"]
    for field, _path, _detection in samples:
        assert fields[field]["value"]["label"] == "Farm"
        assert fields[field]["value"]["label_ocr_confidence"] >= 90.0


def test_post_run_attack_dissonance_preset_restores_run_identity():
    observer = NoStrategyRunObserver(clock=_clock)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected = SimpleNamespace(selected=True, green_pixels=2489, cyan_pixels=290)
    unselected = SimpleNamespace(selected=False, green_pixels=0, cyan_pixels=0)

    with (
        patch(
            "core.no_strategy_observer.measure_preset_slot_selection",
            side_effect=[selected, unselected, unselected, unselected, unselected],
        ),
        patch(
            "core.no_strategy_observer.ocr_text_and_conf",
            return_value=("Attack Disso", 92.0),
        ),
    ):
        observer.observe(frame, {"state": "WORKSHOP"}, phase="post_run_home")

    fields = observer.snapshot()["fields"]
    identity = fields["run_identity"]
    assert identity["status"] == "observed"
    assert identity["value"]["label"] == "Attack Dissonance"
    assert identity["source"] == "post_run_workshop_preset_selected_border"
    assert identity["phase"] == "post_run_home"


def test_post_run_utility_dissonance_preset_restores_run_identity():
    observer = NoStrategyRunObserver(clock=_clock)
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected = SimpleNamespace(selected=True, green_pixels=2489, cyan_pixels=290)
    unselected = SimpleNamespace(selected=False, green_pixels=0, cyan_pixels=0)

    with (
        patch(
            "core.no_strategy_observer.measure_preset_slot_selection",
            side_effect=[selected, unselected, unselected, unselected, unselected],
        ),
        patch(
            "core.no_strategy_observer.ocr_text_and_conf",
            return_value=("Util Disso", 96.0),
        ),
    ):
        observer.observe(frame, {"state": "WORKSHOP"}, phase="post_run_home")

    identity = observer.snapshot()["fields"]["run_identity"]
    assert identity["status"] == "observed"
    assert identity["value"]["subtype"] == "Utility"
    assert identity["value"]["label"] == "Utility Dissonance"


def test_post_run_utility_preset_refines_generic_dissonance_badge():
    observer = NoStrategyRunObserver(clock=_clock)
    generic = np.zeros((1920, 1080, 3), dtype=np.uint8)
    purple = cv2.cvtColor(
        np.uint8([[[145, 220, 220]]]), cv2.COLOR_HSV2BGR
    )[0, 0]
    x, y, width, height = DISSONANCE_BADGE_REGION
    generic[y : y + height, x : x + width] = purple
    observer.observe(
        generic,
        {"state": "RUNNING", "menu": "UTILITY_MENU", "secondary_states": []},
    )
    selected = SimpleNamespace(selected=True, green_pixels=2489, cyan_pixels=290)
    unselected = SimpleNamespace(selected=False, green_pixels=0, cyan_pixels=0)

    with (
        patch(
            "core.no_strategy_observer.measure_preset_slot_selection",
            side_effect=[selected, unselected, unselected, unselected, unselected],
        ),
        patch(
            "core.no_strategy_observer.ocr_text_and_conf",
            return_value=("Util Disso", 96.0),
        ),
    ):
        observer.observe(
            np.zeros((1920, 1080, 3), dtype=np.uint8),
            {"state": "WORKSHOP"},
            phase="post_run_home",
        )

    identity = observer.snapshot()["fields"]["run_identity"]
    assert identity["value"]["label"] == "Utility Dissonance"
    assert identity["source"] == "post_run_workshop_preset_selected_border"


def test_post_run_preset_does_not_replace_stronger_live_badge_identity():
    observer = NoStrategyRunObserver(clock=_clock)
    observer.observe(
        _utility_dissonance_frame(),
        {"state": "RUNNING", "menu": "UTILITY_MENU", "secondary_states": []},
    )
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    selected = SimpleNamespace(selected=True, green_pixels=2489, cyan_pixels=290)
    unselected = SimpleNamespace(selected=False, green_pixels=0, cyan_pixels=0)

    with (
        patch(
            "core.no_strategy_observer.measure_preset_slot_selection",
            side_effect=[selected, unselected, unselected, unselected, unselected],
        ),
        patch(
            "core.no_strategy_observer.ocr_text_and_conf",
            return_value=("Attack Disso", 96.0),
        ),
    ):
        observer.observe(frame, {"state": "WORKSHOP"}, phase="post_run_home")

    identity = observer.snapshot()["fields"]["run_identity"]
    assert identity["value"]["label"] == "Utility Dissonance"
    assert identity["source"] == "tier_utility_dissonance_badge"


def test_unknown_post_run_field_is_rejected():
    observer = NoStrategyRunObserver(clock=_clock)

    try:
        observer.record_post_run_value("profile", "farm", source="guess")
    except ValueError as exc:
        assert "unknown No Strategy observation field" in str(exc)
    else:
        raise AssertionError("unknown observation field was accepted")
