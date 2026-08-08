from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from automation.strategies import get_strategy
from core.app import HOME_SETUP_MAX_ATTEMPTS, App
from core.adb_target_session import AdbTargetSnapshot
from core.battle_lifecycle import HomeBattleControl
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_no_battle_setup import (
    GcNoBattleSetupResult,
    GcNoBattleSetupStatus,
    _is_not_enough_medals_dialog,
    _replace_guardian_chip,
    run_gc_no_battle_setup,
)
from core.gc_module_loadout import ModuleLoadoutCorrectionError
from core.gate_decisions import build_gate_decision_options
from core.home_battle import HomeBattleEvidence
from core.home_perk_configuration import (
    HomePerkConfigurationRepairExhausted,
    HomePerkConfigurationResult,
)
from core.matcher import get_match
from core.perk_configuration import FARM_AUTO_PICK_ORDER, FARM_PERK_BANS
from core.poison_swamp_stun import PoisonSwampStunState
from core.player_save_preflight import CarriedEvidenceState
from core.run_state import AUTOMATION, ExecMode
from core.target_priority import TARGETS
from core.workshop_preset import (
    BOTS_AMPLIFY_PRESET_SLOT,
    BOTS_FARM_PRESET_SLOT,
    CARDS_FARM_PRESET_SLOT,
    CARDS_TOURNAMENT_PRESET_SLOT,
    FARM_PRESET_SLOT,
    PresetSlotSelection,
    TOURNEY_PRESET_SLOT,
)
from core.tournament_preflight import (
    TOURNAMENT_SECTION_SPECS,
    load_tournament_requirements,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures"
REQUIREMENTS = {
    "cards_deck": "Farm",
    "workshop_preset": "Farm",
    "bots_preset": "Farm",
    "guardian_chips": ["Fetch", "Summon", "Scout"],
    "modules": {
        "cannon_assist": "Being Annihilator",
        "cannon_primary": "Amplifying Strike",
        "generator_primary": "Black Hole Digestor",
        "generator_assist": "Singularity Harness",
        "armor_assist": "Anti-Cube Portal",
        "armor_primary": "Orbital Augment",
        "core_primary": "Multiverse Nexus",
        "core_assist": "Dimension Core",
    },
    "target_priority": list(TARGETS),
    "loadout_policies": {
        "modules": "enforce",
        "target_priority": "enforce",
    },
}
TOURNAMENT_REQUIREMENTS = load_tournament_requirements()


class _NoBattleRouter:
    def __init__(
        self,
        *,
        selected: bool = False,
        correct_guardians: bool = False,
        missing_scout: bool = False,
        bots_offscreen: bool = False,
        deny_bots_preset: bool = False,
    ):
        self.state = "home"
        self.selected = {
            CARDS_FARM_PRESET_SLOT: selected,
            FARM_PRESET_SLOT: selected,
            BOTS_FARM_PRESET_SLOT: selected,
        }
        self.guardians = (
            {"fetch", "summon", "scout"}
            if correct_guardians
            else {"attack", "ally"} | (set() if missing_scout else {"scout"})
        )
        self.static_actions = []
        self.visible_actions = []
        self.module_checks = []
        self.module_observations = []
        self.card_recharge_checks = []
        self.card_recharge_screenshot = "cards"
        self.configuration_screens = []
        self.bots_offscreen = bots_offscreen
        self.deny_bots_preset = deny_bots_preset
        self.swipes = []

    def capture(self):
        return self.state

    def detect(self, frame):
        assert frame == self.state
        if frame == "home":
            return {"state": "HOME_SCREEN", "secondary_states": []}
        if frame in {"cards", "cards_after_recharge"}:
            return {"state": "CARDS", "secondary_states": ["CARDS_FARM_SLOT"]}
        if frame == "workshop":
            return {
                "state": "WORKSHOP",
                "secondary_states": ["WORKSHOP_FARM_SLOT"],
            }
        if frame == "workshop_uw":
            return {
                "state": "WORKSHOP",
                "secondary_states": ["WORKSHOP_FARM_SLOT"],
            }
        if frame == "event":
            return {"state": "EVENT", "secondary_states": []}
        if frame == "bots":
            secondary = [] if self.bots_offscreen else [
                "EVENT_BOTS_SCREEN",
                "BOTS_FARM_SLOT",
            ]
            return {
                "state": "EVENT",
                "secondary_states": secondary,
            }
        if frame == "medals_dialog":
            return {
                "state": "EVENT",
                "secondary_states": ["EVENT_BOTS_SCREEN", "BOTS_FARM_SLOT"],
            }
        if frame == "guild":
            return {"state": "GUILD", "secondary_states": []}
        if frame == "guardians":
            secondary = ["GUILD_GUARDIAN_SCREEN"]
            for chip in ("attack", "ally", "fetch", "summon", "scout"):
                if chip in self.guardians:
                    secondary.append(f"GUARDIAN_{chip.upper()}_EQUIPPED")
            return {"state": "GUILD", "secondary_states": secondary}
        if frame == "modules":
            return {"state": "MODULES", "secondary_states": []}
        raise AssertionError(frame)

    def home_control(self, _frame):
        return HomeBattleEvidence(HomeBattleControl.NEW_BATTLE, "test", 100.0)

    def measure(self, _frame, region):
        selected = self.selected[region]
        return PresetSlotSelection(
            region,
            True,
            selected,
            2000 if selected else 0,
            0 if selected else 2000,
        )

    def static_tap(self, label, **_kwargs):
        self.static_actions.append(label)
        if (
            label == "buttons.guardian:scout_inventory"
            and self.state == "guardians"
        ):
            self.guardians.add("scout")
            return True
        if label == (540, 1100) and self.state == "medals_dialog":
            self.state = "bots"
            return True
        transitions = {
            ("home", "navigation.goto_cards_home"): "cards",
            ("home", "navigation.goto_workshop_home"): "workshop",
            ("event", "navigation.event:bots_tab"): "bots",
            ("guild", "navigation.guild:guardian_tab"): "guardians",
            ("home", "navigation.goto_modules_home"): "modules",
        }
        if label == "navigation.goto_home" and self.state != "home":
            self.state = "home"
            return True
        destination = transitions.get((self.state, label))
        if destination is None:
            return False
        self.state = destination
        return True


    def ensure_modules(self, requirements, **kwargs):
        assert self.state == "modules"
        assert kwargs["screenshot"] == "modules"
        self.module_checks.append(dict(requirements))
        return SimpleNamespace(
            valid=True,
            as_dict=lambda: {"valid": True, "slots": []},
        )

    def ensure_card_recharge_modes(self, requirements, **kwargs):
        assert self.state == "cards"
        assert kwargs["cards_screenshot"] == "cards"
        self.card_recharge_checks.append(dict(requirements))
        self.state = self.card_recharge_screenshot
        return SimpleNamespace(
            valid=True,
            screenshot=self.card_recharge_screenshot,
            as_dict=lambda: {
                "valid": True,
                "changed": False,
                "changed_labels": [],
                "modes": [
                    {
                        "label": label,
                        "required": mode,
                        "observed": mode,
                        "valid": True,
                    }
                    for label, mode in requirements.items()
                ],
            },
        )

    def evaluate_modules(self, _screen, requirements):
        assert self.state == "modules"
        self.module_observations.append(dict(requirements))
        return SimpleNamespace(
            valid=True,
            as_dict=lambda: {"valid": True, "slots": []},
        )

    def validate_configuration(self, **kwargs):
        self.configuration_screens.append(
            {
                key: kwargs[key]
                for key in (
                    "cards_screen",
                    "workshop_screen",
                    "bots_screen",
                    "guardians_screen",
                )
            }
        )
        return SimpleNamespace(
            valid=True,
            as_dict=lambda: {"valid": True},
        )

    def select_workshop_menu(self, current, menu, **_kwargs):
        assert current == "workshop"
        assert menu == "ultimate weapons"
        self.state = "workshop_uw"
        return self.state

    def ensure_stun(self, **kwargs):
        assert self.state == "workshop_uw"
        assert kwargs["screenshot"] == "workshop_uw"
        required_state = PoisonSwampStunState(kwargs["required_state"])
        return SimpleNamespace(
            screenshot="workshop_uw",
            evidence=SimpleNamespace(state=required_state),
            changed=False,
        )

    def swipe(self, label):
        self.swipes.append(label)
        if self.state == "bots" and label == "gesture_targets.goto_top:event_bots":
            self.bots_offscreen = False
            return True
        return True

    def visible_tap(self, label, **_kwargs):
        self.visible_actions.append(label)
        if self.state == "cards" and label == "indicators.cards:farm_slot":
            self.selected[CARDS_FARM_PRESET_SLOT] = True
            return True
        if self.state == "workshop" and label == "indicators.workshop:farm_slot":
            self.selected[FARM_PRESET_SLOT] = True
            return True
        if self.state == "home" and label == "navigation.home_event":
            self.state = "event"
            return True
        if self.state == "bots" and label == "indicators.bots:farm_slot":
            if self.deny_bots_preset:
                self.state = "medals_dialog"
                return True
            self.selected[BOTS_FARM_PRESET_SLOT] = True
            return True
        if self.state in {"bots", "guardians"} and label == "buttons.return_to_game":
            self.state = "home"
            return True
        if self.state == "home" and label == "navigation.home_guild":
            self.state = "guild"
            return True
        if self.state != "guardians":
            return False
        guardian_actions = {
            "indicators.guardian:attack_equipped": ("attack", None),
            "buttons.guardian:fetch_inventory": (None, "fetch"),
            "indicators.guardian:ally_equipped": ("ally", None),
            "buttons.guardian:summon_inventory": (None, "summon"),
            "buttons.guardian:scout_inventory": (None, "scout"),
        }
        remove, add = guardian_actions.get(label, ("unknown", "unknown"))
        if remove == "unknown":
            return False
        if remove:
            if remove not in self.guardians:
                return False
            self.guardians.remove(remove)
        if add:
            self.guardians.add(add)
        return True


class _TournamentRouter(_NoBattleRouter):
    def __init__(self, *, selected: bool = False, correct_guardians: bool = False):
        super().__init__(selected=True, correct_guardians=True)
        self.selected = {
            CARDS_TOURNAMENT_PRESET_SLOT: selected,
            TOURNEY_PRESET_SLOT: selected,
            BOTS_AMPLIFY_PRESET_SLOT: selected,
        }
        self.guardians = (
            {"attack", "ally", "scout"}
            if correct_guardians
            else {"fetch", "summon", "scout"}
        )
        self.configuration_specs = None

    def detect(self, frame):
        if frame == "cards":
            return {
                "state": "CARDS",
                "secondary_states": ["CARDS_TOURNAMENT_SLOT"],
            }
        if frame == "workshop":
            return {
                "state": "WORKSHOP",
                "secondary_states": ["WORKSHOP_TOURNEY_SLOT"],
            }
        if frame == "bots":
            return {
                "state": "EVENT",
                "secondary_states": ["EVENT_BOTS_SCREEN", "BOTS_AMPLIFY_SLOT"],
            }
        if frame == "guardians":
            secondary = ["GUILD_GUARDIAN_SCREEN"]
            for chip in ("attack", "ally", "scout", "fetch", "summon"):
                if chip in self.guardians:
                    secondary.append(f"GUARDIAN_{chip.upper()}_EQUIPPED")
            return {"state": "GUILD", "secondary_states": secondary}
        return super().detect(frame)

    def visible_tap(self, label, **kwargs):
        if self.state == "cards" and label == "indicators.cards:tournament_slot":
            self.visible_actions.append(label)
            self.selected[CARDS_TOURNAMENT_PRESET_SLOT] = True
            return True
        if self.state == "workshop" and label == "indicators.workshop:tourney_slot":
            self.visible_actions.append(label)
            self.selected[TOURNEY_PRESET_SLOT] = True
            return True
        if self.state == "bots" and label == "indicators.bots:amplify_slot":
            self.visible_actions.append(label)
            self.selected[BOTS_AMPLIFY_PRESET_SLOT] = True
            return True
        if self.state == "guardians" and label in {
            "indicators.guardian:fetch_equipped",
            "indicators.guardian:summon_equipped",
        }:
            self.visible_actions.append(label)
            chip = "fetch" if "fetch" in label else "summon"
            self.guardians.remove(chip)
            return True
        if self.state == "guardians" and label in {
            "buttons.guardian:attack_inventory",
            "buttons.guardian:ally_inventory",
        }:
            self.visible_actions.append(label)
            chip = "attack" if "attack" in label else "ally"
            self.guardians.add(chip)
            return True
        return super().visible_tap(label, **kwargs)

    def validate_configuration(self, **kwargs):
        self.configuration_specs = kwargs.get("section_specs")
        return super().validate_configuration(**kwargs)


def _run(
    router,
    requirements=REQUIREMENTS,
    *,
    waivers=None,
    save_decisions=None,
    snapshot_invalidation_fn=None,
    save_ui_verification_fn=None,
    ensure_perk_configuration_fn=None,
    action_guard_fn=None,
):
    kwargs = {}
    if ensure_perk_configuration_fn is not None:
        kwargs["ensure_perk_configuration_fn"] = ensure_perk_configuration_fn
    return run_gc_no_battle_setup(
        requirements,
        screenshot="home",
        waivers=waivers,
        save_decisions=save_decisions,
        snapshot_invalidation_fn=snapshot_invalidation_fn,
        save_ui_verification_fn=save_ui_verification_fn,
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        swipe_fn=router.swipe,
        measure_selection_fn=router.measure,
        ensure_modules_fn=router.ensure_modules,
        evaluate_modules_fn=router.evaluate_modules,
        select_workshop_menu_fn=router.select_workshop_menu,
        ensure_poison_swamp_stun_fn=router.ensure_stun,
        ensure_card_recharge_modes_fn=router.ensure_card_recharge_modes,
        validate_configuration_fn=router.validate_configuration,
        action_guard_fn=action_guard_fn,
        sleep_fn=lambda _seconds: None,
        **kwargs,
    )


def _save_matches(requirements, *check_ids):
    return {
        check_id: {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
            "expected": requirements.get(check_id),
            "observed": requirements.get(check_id),
            "ui_required": False,
        }
        for check_id in check_ids
    }


def _save_mismatches(requirements, *check_ids):
    return {
        check_id: {
            "disposition": "save_mismatch",
            "reason": "save_mismatch",
            "expected": requirements.get(check_id),
            "observed": f"different-{check_id}",
            "snapshot_trusted": True,
            "save_evidence_complete": True,
            "save_check_validated": True,
            "save_requirement_supported": True,
            "ui_required": True,
            "ui_requirement_kind": "trusted_mismatch",
            "repair_queued": True,
        }
        for check_id in check_ids
    }


def test_matching_save_snapshot_skips_all_eligible_home_navigation_together():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "card_recharge_modes": {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        },
        "workshop_preset": "Farm",
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "auto_pick_perks": True,
        "perk_first_choice": "perk_wave_requirement",
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
        "ultimate_weapons": {
            "Poison Swamp": {"primary": "on", "stun": "off"},
        },
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    save_decisions = _save_matches(
        requirements,
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "free_upgrade_locks",
        "bots_preset",
        "guardian_chips",
        "auto_pick_perks",
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    )
    save_decisions["poison_swamp_stun"] = {
        "disposition": "save_match",
        "reason": "exact_version_save_match",
        "expected": "off",
        "observed": "off",
        "ui_required": False,
    }

    result = _run(
        router,
        requirements,
        save_decisions=save_decisions,
    )

    assert result.complete
    assert router.state == "home"
    assert router.static_actions == []
    assert router.visible_actions == []
    assert router.module_checks == []
    assert router.card_recharge_checks == []
    assert router.configuration_screens == [
        {
            "cards_screen": None,
            "workshop_screen": None,
            "bots_screen": None,
            "guardians_screen": None,
        }
    ]
    assert result.evidence["configuration"]["save_backed_sections"] == {
        "bots": {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
        },
        "cards": {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
        },
        "guardians": {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
        },
        "workshop": {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
        },
    }


def test_exact_farm_module_save_match_skips_modules_navigation():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    save_decisions = _save_matches(
        REQUIREMENTS,
        "cards_deck",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
        "modules",
        "target_priority",
    )
    save_decisions["modules"].update(
        mapping_id="data-9-game-1073",
        save_evidence_complete=True,
        save_requirement_supported=True,
        diagnostics={"slots": 8},
    )

    result = _run(
        router,
        REQUIREMENTS,
        save_decisions=save_decisions,
    )

    assert result.complete
    assert router.module_checks == []
    assert "navigation.goto_modules_home" not in router.static_actions
    modules = result.evidence["modules"]
    assert modules["source"] == "player_save_preflight"
    assert modules["status"] == "save_match"
    assert modules["checked"] is False
    assert modules["valid"] is True
    assert len(modules["slots"]) == 8
    assert all(slot["valid"] for slot in modules["slots"])


def test_tournament_save_observation_reports_variation_without_modules_ui():
    router = _TournamentRouter(selected=True, correct_guardians=True)
    observed = {
        **TOURNAMENT_REQUIREMENTS["modules"],
        "armor_primary": "Anti-Cube Portal",
        "armor_assist": "Space Displacer",
    }
    save_decisions = {
        "modules": {
            "disposition": "save_observation",
            "reason": "exact_version_save_observation",
            "expected": TOURNAMENT_REQUIREMENTS["modules"],
            "observed": observed,
            "mapping_id": "data-9-game-1073",
            "save_evidence_complete": True,
            "save_requirement_supported": True,
            "ui_required": False,
        }
    }

    result = _run(
        router,
        TOURNAMENT_REQUIREMENTS,
        save_decisions=save_decisions,
    )

    assert result.complete
    assert router.module_checks == []
    assert router.module_observations == []
    assert "navigation.goto_modules_home" not in router.static_actions
    modules = result.evidence["modules"]
    assert modules["status"] == "save_observation"
    assert modules["mode"] == "observe"
    assert modules["checked"] is False
    assert modules["valid"] is False
    assert modules["fully_observed"] is True
    assert {
        slot["slot_key"]: slot["actual"]
        for slot in modules["slots"]
    } == observed


def test_save_backed_required_locks_ignore_unmanaged_health_without_input():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    decision = _save_matches(
        requirements,
        "cards_deck",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
    )
    decision.update({
        "free_upgrade_locks": {
            "disposition": "save_match",
            "reason": "exact_version_save_match",
            "expected": list(FARM_FREE_UPGRADE_LOCKS),
            "observed": [*FARM_FREE_UPGRADE_LOCKS, "Health"],
            "diagnostics": {"unmanaged_locks": ["Health"]},
            "ui_required": False,
        }
    })
    lock_inputs = []

    result = run_gc_no_battle_setup(
        requirements,
        screenshot="home",
        save_decisions=decision,
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        ensure_free_upgrade_locks_fn=lambda *_args, **_kwargs: (
            lock_inputs.append(True)
        ),
        validate_configuration_fn=router.validate_configuration,
        sleep_fn=lambda _seconds: None,
    )

    assert result.complete
    assert lock_inputs == []
    locks = result.evidence["free_upgrade_locks"]
    assert locks["valid"] is True
    assert locks["required"] == list(FARM_FREE_UPGRADE_LOCKS)
    assert locks["observed"] == [*FARM_FREE_UPGRADE_LOCKS, "Health"]
    assert locks["diagnostics"]["unmanaged_locks"] == ["Health"]


def test_cards_mismatch_repair_preserves_save_backed_perk_decisions():
    router = _NoBattleRouter(selected=False, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "perk_first_choice": "perk_wave_requirement",
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    invalidations = []
    save_decisions = _save_matches(
        requirements,
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    )
    save_decisions.update(_save_mismatches(requirements, "cards_deck"))
    ensure_perks = Mock(
        side_effect=AssertionError("accepted Perks tabs must remain closed")
    )

    result = _run(
        router,
        requirements,
        save_decisions=save_decisions,
        snapshot_invalidation_fn=invalidations.append,
        ensure_perk_configuration_fn=ensure_perks,
    )

    assert result.complete
    assert invalidations == []
    ensure_perks.assert_not_called()
    assert "navigation.goto_cards_home" in router.static_actions
    assert "navigation.goto_workshop_home" not in router.static_actions
    assert "navigation.event:bots_tab" not in router.static_actions
    assert "navigation.guild:guardian_tab" not in router.static_actions
    assert result.evidence["cards_deck"]["status"] == "ui_verified_repair"
    assert result.evidence["cards_deck"]["source"] == "ui"
    assert result.evidence["cards_deck"]["save_disposition"] == "save_mismatch"
    assert result.evidence["save_preflight"]["invalidated"] is False
    assert result.evidence["save_preflight"]["remaining_accepted_checks"] == [
        "bots_preset",
        "guardian_chips",
        "perk_auto_pick_order",
        "perk_bans",
        "perk_first_choice",
        "workshop_preset",
    ]
    for check_id in (
        "perk_first_choice",
        "perk_bans",
        "perk_auto_pick_order",
    ):
        assert result.evidence[check_id]["source"] == "player_save_preflight"


def test_multiple_trusted_mismatches_run_only_their_ui_repair_paths():
    router = _NoBattleRouter(selected=False, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    decisions = _save_matches(requirements, "guardian_chips")
    decisions.update(
        _save_mismatches(
            requirements,
            "cards_deck",
            "workshop_preset",
            "bots_preset",
        )
    )

    result = _run(router, requirements, save_decisions=decisions)

    assert result.complete
    assert "navigation.goto_cards_home" in router.static_actions
    assert "navigation.goto_workshop_home" in router.static_actions
    assert "navigation.event:bots_tab" in router.static_actions
    assert "navigation.guild:guardian_tab" not in router.static_actions
    assert result.evidence["save_preflight"]["ui_verified_checks"] == {
        "bots_preset": "ui_verified_repair",
        "cards_deck": "ui_verified_repair",
        "workshop_preset": "ui_verified_repair",
    }
    assert result.evidence["save_preflight"]["remaining_accepted_checks"] == [
        "guardian_chips"
    ]


def test_save_mismatch_ui_already_matches_is_a_contradiction():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    decisions = _save_matches(
        requirements,
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
    )
    decisions.update(_save_mismatches(requirements, "cards_deck"))
    invalidations = []

    result = _run(
        router,
        requirements,
        save_decisions=decisions,
        snapshot_invalidation_fn=invalidations.append,
    )

    assert not result.complete
    assert result.failed_check == "cards_deck"
    assert "contradicted authoritative UI" in result.reason
    assert invalidations == ["save_ui_contradiction"]
    assert result.evidence["save_preflight"]["invalidated"] is True
    assert result.evidence["save_preflight"]["contradictions"] == [
        "cards_deck"
    ]
    assert result.evidence["save_preflight"]["remaining_accepted_checks"] == []


def test_mixed_perk_decisions_visit_only_ui_required_tabs():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "perk_first_choice": "perk_wave_requirement",
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": ["game_speed"],
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    save_decisions = _save_matches(
        requirements,
        "cards_deck",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
        "perk_first_choice",
        "perk_auto_pick_order",
    )
    inspected = []

    def ensure_perks(_requirements, **kwargs):
        inspected.append(set(kwargs["waived_fields"]))
        return HomePerkConfigurationResult(
            valid=True,
            changed=False,
            reason="matched",
            failed_check=None,
            evidence={
                "perk_bans": {
                    "checked": True,
                    "valid": True,
                    "observed": list(FARM_PERK_BANS),
                }
            },
            home_screenshot="home",
        )

    result = _run(
        router,
        requirements,
        save_decisions=save_decisions,
        ensure_perk_configuration_fn=ensure_perks,
    )

    assert result.complete
    assert inspected == [
        {"perk_first_choice", "perk_auto_pick_order"}
    ]
    assert result.evidence["perk_first_choice"]["source"] == (
        "player_save_preflight"
    )
    assert result.evidence["perk_auto_pick_order"]["source"] == (
        "player_save_preflight"
    )
    assert result.evidence["perk_bans"]["checked"] is True


def test_no_battle_setup_corrects_supported_farm_presets_and_guardians():
    router = _NoBattleRouter()

    result = _run(router)

    assert result.complete
    assert router.state == "home"
    assert all(router.selected.values())
    assert router.guardians == {"fetch", "summon", "scout"}
    assert router.module_checks == [REQUIREMENTS["modules"]]
    assert result.evidence["target_priority"] == {
        "mode": "enforce",
        "checked": False,
        "valid": None,
        "boundary": "RUNNING",
        "reason": "battle_only_control",
    }
    assert router.visible_actions == [
        "indicators.cards:farm_slot",
        "indicators.workshop:farm_slot",
        "navigation.home_event",
        "indicators.bots:farm_slot",
        "buttons.return_to_game",
        "navigation.home_guild",
        "indicators.guardian:attack_equipped",
        "buttons.guardian:fetch_inventory",
        "indicators.guardian:ally_equipped",
        "buttons.guardian:summon_inventory",
        "buttons.return_to_game",
    ]


def test_no_battle_setup_enforces_card_recharge_modes_before_leaving_cards():
    router = _NoBattleRouter()
    requirements = {
        **REQUIREMENTS,
        "card_recharge_modes": {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        },
    }

    result = _run(router, requirements)

    assert result.complete
    assert router.card_recharge_checks == [
        {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        }
    ]
    assert result.evidence["card_recharge_modes"]["valid"] is True
    assert result.evidence["card_recharge_modes"]["modes"] == [
        {
            "label": "Demon Mode",
            "required": "auto_reactivate",
            "observed": "auto_reactivate",
            "valid": True,
        },
        {
            "label": "Nuke",
            "required": "ready_after_recharge",
            "observed": "ready_after_recharge",
            "valid": True,
        },
    ]


def test_no_battle_setup_retains_verified_preset_frame_after_card_scan():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    router.card_recharge_screenshot = "cards_after_recharge"
    requirements = {
        **REQUIREMENTS,
        "card_recharge_modes": {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        },
    }

    result = _run(router, requirements)

    assert result.complete
    assert router.configuration_screens[-1]["cards_screen"] == "cards"


def test_no_battle_setup_blocks_contradictory_boundary_evidence():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    def contradictory_configuration(**_kwargs):
        return SimpleNamespace(
            valid=False,
            as_dict=lambda: {
                "cards": {"valid": False},
                "workshop": {"valid": True},
                "bots": {"valid": True},
                "guardians": {"valid": True},
                "valid": False,
            },
        )

    router.validate_configuration = contradictory_configuration

    result = _run(router)

    assert not result.complete
    assert result.failed_check == "cards_deck"
    assert "contradicted the completed checks: cards_deck" in result.reason


def test_ui_contradiction_to_save_match_invalidates_the_snapshot():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "card_recharge_modes": {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        },
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    decisions = _save_matches(
        requirements,
        "cards_deck",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
    )
    invalidations = []

    router.validate_configuration = lambda **_kwargs: SimpleNamespace(
        valid=False,
        as_dict=lambda: {
            "cards": {"valid": False},
            "workshop": {"valid": True},
            "bots": {"valid": True},
            "guardians": {"valid": True},
            "valid": False,
        },
    )

    result = _run(
        router,
        requirements,
        save_decisions=decisions,
        snapshot_invalidation_fn=invalidations.append,
    )

    assert not result.complete
    assert result.failed_check == "cards_deck"
    assert invalidations == ["save_ui_contradiction"]
    assert result.evidence["save_preflight"]["invalidated"] is True
    assert result.evidence["save_preflight"]["contradictions"] == [
        "cards_deck"
    ]


def test_no_battle_setup_allows_waived_boundary_mismatch():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    def waived_configuration(**_kwargs):
        return SimpleNamespace(
            valid=False,
            as_dict=lambda: {
                "cards": {"valid": False},
                "workshop": {"valid": True},
                "bots": {"valid": True},
                "guardians": {"valid": True},
                "valid": False,
            },
        )

    router.validate_configuration = waived_configuration

    result = _run(
        router,
        waivers={"cards_deck": {"label": "skip cards", "reason": "test"}},
    )

    assert result.complete
    assert result.evidence["configuration"]["blocking_valid"] is True


def test_no_battle_setup_blocks_inputs_during_pause_then_restores_home():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    guard_results = iter([True, False, False, True, True])

    def action_guard():
        return next(guard_results, True)

    result = _run(router, action_guard_fn=action_guard)

    assert result.status is GcNoBattleSetupStatus.INTERRUPTED
    assert router.state == "home"
    assert router.static_actions == [
        "navigation.goto_cards_home",
        "navigation.goto_home",
    ]


def test_no_battle_setup_applies_strategy_owned_perk_configuration():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
    }
    evidence = {
        "valid": True,
        "failed_checks": [],
        "perk_bans": {
            "valid": True,
            "expected_labels": ["Cash Trade-Off"],
            "observed_labels": ["Cash Trade-Off"],
        },
        "perk_auto_pick_order": {
            "valid": True,
            "expected_labels": ["Coin Trade-Off"],
            "observed_labels": ["Coin Trade-Off"],
        },
    }
    ensure = Mock(
        return_value=HomePerkConfigurationResult(
            valid=True,
            changed=True,
            reason="strategy Perk configuration verified",
            failed_check=None,
            evidence=evidence,
            home_screenshot="home",
        )
    )

    result = _run(
        router,
        requirements,
        ensure_perk_configuration_fn=ensure,
    )

    assert result.complete
    ensure.assert_called_once()
    assert ensure.call_args.kwargs["home_screenshot"] == "home"
    assert ensure.call_args.kwargs["operator_workflow"] is False
    assert result.evidence["perk_bans"]["changed"] is True
    assert result.evidence["perk_auto_pick_order"]["changed"] is True


def test_no_battle_setup_permanent_perk_skips_do_not_open_configuration():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
    }
    ensure = Mock()
    waivers = {
        check_id: {
            "source": "strategy_profile",
            "scope": "every_run",
        }
        for check_id in ("perk_bans", "perk_auto_pick_order")
    }

    result = _run(
        router,
        requirements,
        waivers=waivers,
        ensure_perk_configuration_fn=ensure,
    )

    assert result.complete
    ensure.assert_not_called()
    assert result.evidence["perk_bans"]["status"] == "waived"
    assert result.evidence["perk_auto_pick_order"]["status"] == "waived"


def test_invalid_strategy_perk_configuration_blocks_before_workshop():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
    }
    ensure = Mock(
        return_value=HomePerkConfigurationResult(
            valid=False,
            changed=True,
            reason="Coin Trade-Off remained below rank 3",
            failed_check="perk_auto_pick_order",
            evidence={
                "valid": False,
                "failed_checks": ["perk_auto_pick_order"],
                "perk_bans": {
                    "valid": True,
                    "expected_labels": [],
                    "observed_labels": [],
                },
                "perk_auto_pick_order": {
                    "valid": False,
                    "expected_labels": ["Coin Trade-Off"],
                    "observed_labels": [],
                },
            },
            home_screenshot="home",
        )
    )

    result = _run(
        router,
        requirements,
        ensure_perk_configuration_fn=ensure,
    )

    assert result.status is GcNoBattleSetupStatus.FAILED
    assert result.failed_check == "perk_auto_pick_order"
    assert "navigation.goto_workshop_home" not in router.static_actions


def test_exhausted_local_perk_repair_is_not_retryable_from_home():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
    }
    ensure = Mock(
        side_effect=HomePerkConfigurationRepairExhausted(
            "Ban Perks made no stable progress"
        )
    )

    result = _run(
        router,
        requirements,
        ensure_perk_configuration_fn=ensure,
    )

    assert result.status is GcNoBattleSetupStatus.FAILED
    assert result.failed_check == "perk_configuration"
    assert result.retryable_from_home is False
    ensure.assert_called_once()


def test_home_preflight_logs_concise_check_results_for_operator_activity():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    with (
        patch("core.gc_no_battle_setup.log") as emit,
        patch("core.gc_no_battle_setup.log_action_intent") as action_log,
        patch("core.gc_no_battle_setup.log_result") as result_log,
    ):
        result = _run(router)

    assert result.complete
    messages = [entry.args[0] for entry in emit.call_args_list]
    assert any(
        "[HOME_PREFLIGHT] Cards deck passed; "
        "expected=Farm; observed=Farm" in message
        for message in messages
    )
    assert any(
        "[HOME_PREFLIGHT] Modules passed; "
        "expected=8 configured assignments" in message
        for message in messages
    )
    assert any(
        "[HOME_PREFLIGHT] Target Priority deferred; "
        "expected=10 configured priorities; "
        "observed=in-battle control pending" in message
        for message in messages
    )
    action_log.assert_called_once()
    assert action_log.call_args.args[0] == (
        "Verifying Home-only run configuration"
    )
    assert action_log.call_args.kwargs["reason"] == (
        "check strategy-owned persistent settings before restart and repair "
        "only authoritative mismatches"
    )
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Home-only run configuration complete — verified without changes"
    )


def test_save_accepted_mapping_and_list_logs_render_normalized_values():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        "cards_deck": "Farm",
        "card_recharge_modes": {
            "Demon Mode": "auto_reactivate",
            "Nuke": "ready_after_recharge",
        },
        "workshop_preset": "Farm",
        "bots_preset": "Farm",
        "guardian_chips": ["Fetch", "Summon", "Scout"],
        "perk_bans": list(FARM_PERK_BANS),
        "perk_auto_pick_order": list(FARM_AUTO_PICK_ORDER),
        "loadout_policies": {
            "modules": "preserve",
            "target_priority": "preserve",
        },
    }
    save_decisions = _save_matches(
        requirements,
        "cards_deck",
        "card_recharge_modes",
        "workshop_preset",
        "bots_preset",
        "guardian_chips",
        "perk_bans",
        "perk_auto_pick_order",
    )
    for decision in save_decisions.values():
        decision.update(
            mapping_id="data-9-game-1073",
            save_evidence_complete=True,
            save_requirement_supported=True,
        )

    with patch("core.gc_no_battle_setup.log") as emit:
        result = _run(
            router,
            requirements,
            save_decisions=save_decisions,
        )

    assert result.complete
    messages = [call.args[0] for call in emit.call_args_list]
    card_log = next(
        message
        for message in messages
        if "[HOME_PREFLIGHT] Card recharge modes" in message
    )
    bans_log = next(
        message
        for message in messages
        if "[HOME_PREFLIGHT] Perk Bans" in message
    )
    assert "observed=Demon Mode=auto_reactivate, Nuke=ready_after_recharge" in (
        card_log
    )
    assert "observed=unavailable" not in card_log
    assert "Lifesteal / Knockback Trade-Off" in bans_log
    assert "observed=unavailable" not in bans_log
    assert "mapping=data-9-game-1073" in card_log
    assert "complete=True; supported=True; disposition=save_match" in card_log


def test_home_preflight_result_names_repairs_that_were_applied():
    router = _NoBattleRouter()

    with patch("core.gc_no_battle_setup.log_result") as result_log:
        result = _run(router)

    assert result.complete
    result_log.assert_called_once()
    assert result_log.call_args.args[0] == (
        "Home-only run configuration complete — verified; repairs applied: "
        "Cards deck selected Farm; Workshop preset selected Farm; "
        "Bot preset selected Farm; Guardian Chips equipped Fetch, Summon"
    )


def test_home_preflight_logs_the_failed_requirement_and_reason():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    with (
        patch.object(
            router,
            "ensure_modules",
            side_effect=ModuleLoadoutCorrectionError(
                "failed to select Ancestral rarity"
            ),
        ),
        patch("core.gc_no_battle_setup.log") as emit,
    ):
        result = _run(router)

    assert result.status is GcNoBattleSetupStatus.FAILED
    messages = [entry.args[0] for entry in emit.call_args_list]
    assert any(
        "[HOME_PREFLIGHT] Modules failed; "
        "expected=8 configured assignments; "
        "observed=failed to select Ancestral rarity" in message
        for message in messages
    )


def test_home_preflight_logs_a_one_run_waiver_disposition():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    with patch("core.gc_no_battle_setup.log") as emit:
        result = _run(
            router,
            waivers={
                "bots_preset": {
                    "label": "Continue with Flame for this run",
                    "value": "Flame",
                }
            },
        )

    assert result.complete
    assert any(
        "[HOME_PREFLIGHT] Bot preset waived; expected=Farm; "
        "observed=Continue with Flame for this run" in entry.args[0]
        for entry in emit.call_args_list
    )


def test_guardian_replacement_reacquires_after_empty_slot_settles():
    state = {"phase": "equipped"}
    sleeps = []
    actions = []

    def capture():
        return state["phase"]

    def detect(frame):
        secondary = ["GUILD_GUARDIAN_SCREEN"]
        if frame == "equipped":
            secondary.append("GUARDIAN_ATTACK_EQUIPPED")
        elif frame == "selected":
            secondary.append("GUARDIAN_FETCH_EQUIPPED")
        return {"state": "GUILD", "secondary_states": secondary}

    def visible_tap(label, *, screenshot, retries):
        actions.append((label, screenshot, retries))
        if label == "indicators.guardian:attack_equipped":
            state["phase"] = "transition"
            return True
        if (
            label == "buttons.guardian:fetch_inventory"
            and screenshot == "settled"
        ):
            state["phase"] = "selected"
            return True
        return False

    def sleep(seconds):
        sleeps.append(seconds)
        if state["phase"] == "transition" and seconds >= 1.0:
            state["phase"] = "settled"

    selected = _replace_guardian_chip(
        "equipped",
        wrong_label="indicators.guardian:attack_equipped",
        wrong_secondary="GUARDIAN_ATTACK_EQUIPPED",
        inventory_label="buttons.guardian:fetch_inventory",
        expected_secondary="GUARDIAN_FETCH_EQUIPPED",
        capture_fn=capture,
        detector=detect,
        tap_visible_fn=visible_tap,
        sleep_fn=sleep,
    )

    assert selected == "selected"
    assert 1.0 in sleeps
    assert actions == [
        ("indicators.guardian:attack_equipped", "equipped", 1),
        ("buttons.guardian:fetch_inventory", "settled", 1),
    ]


def test_no_battle_setup_fills_a_missing_scout_guardian_slot():
    router = _NoBattleRouter(selected=True, missing_scout=True)

    result = _run(router)

    assert result.complete
    assert router.guardians == {"fetch", "summon", "scout"}
    assert "buttons.guardian:scout_inventory" in router.visible_actions


def test_no_battle_setup_leaves_already_correct_settings_untouched():
    router = _NoBattleRouter(selected=True, correct_guardians=True)

    result = _run(router)

    assert result.complete
    assert router.module_checks == [REQUIREMENTS["modules"]]
    assert result.evidence["target_priority"]["reason"] == "battle_only_control"
    assert router.visible_actions == [
        "navigation.home_event",
        "buttons.return_to_game",
        "navigation.home_guild",
        "buttons.return_to_game",
    ]


def test_no_battle_setup_verifies_poison_swamp_stun_before_battle():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "ultimate_weapons": {
            "Poison Swamp": {"primary": "on", "stun": "off"},
        },
    }

    result = _run(router, requirements)

    assert result.complete
    assert router.state == "home"
    assert result.evidence["ultimate_weapons"] == {
        "boundary": "NEW_BATTLE",
        "checked": ["Poison Swamp.stun"],
        "observations": {"Poison Swamp": {"stun": "off"}},
        "valid": True,
        "changed": False,
    }
def test_no_battle_setup_corrects_tournament_home_configuration():
    router = _TournamentRouter()

    result = _run(router, TOURNAMENT_REQUIREMENTS)

    assert result.complete
    assert router.state == "home"
    assert all(router.selected.values())
    assert router.guardians == {"attack", "ally", "scout"}
    assert router.module_checks == []
    assert router.module_observations == [TOURNAMENT_REQUIREMENTS["modules"]]
    assert result.evidence["target_priority"] == {
        "mode": "preserve",
        "checked": False,
    }
    assert router.configuration_specs is TOURNAMENT_SECTION_SPECS
    assert result.evidence["cards_deck"] == "Tournament"
    assert result.evidence["workshop_preset"] == "Tourney"
    assert result.evidence["bots_preset"] == "Amplify"
    assert result.evidence["ultimate_weapons"] == {
        "boundary": "NEW_BATTLE",
        "checked": ["Poison Swamp.stun"],
        "observations": {"Poison Swamp": {"stun": "on"}},
        "valid": True,
        "changed": False,
    }
    assert result.evidence["damage_slider"] == {
        "mode": "enforce",
        "value": "1E2%",
        "checked": False,
        "valid": None,
        "boundary": "RUNNING",
        "reason": "battle_only_control",
    }
    assert "buttons.guardian:attack_inventory" in router.visible_actions
    assert "buttons.guardian:ally_inventory" in router.visible_actions


def test_no_battle_setup_resumes_interrupted_tournament_guardian_replacements():
    expected = {"attack", "ally", "scout"}
    for missing_chip, inventory_label in (
        ("attack", "buttons.guardian:attack_inventory"),
        ("ally", "buttons.guardian:ally_inventory"),
    ):
        router = _TournamentRouter(selected=True, correct_guardians=True)
        router.guardians.remove(missing_chip)

        result = _run(router, TOURNAMENT_REQUIREMENTS)

        assert result.complete
        assert router.guardians == expected
        assert inventory_label in router.visible_actions


def test_tournament_guardian_inventory_actions_have_explicit_geometry():
    from core.clickmap_access import get_click

    assert get_click("buttons.guardian:attack_inventory") == (195, 1230)
    assert get_click("buttons.guardian:ally_inventory") == (540, 1230)
    assert get_click("buttons.guardian:scout_inventory") == (870, 1540)


def test_not_enough_medals_dialog_requires_exact_high_confidence_text():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)

    assert _is_not_enough_medals_dialog(
        frame,
        text_fn=lambda *_args, **_kwargs: (
            "NOT ENOUGH MEDALS You need 240 medals to switch presets",
            95.7,
        ),
    )
    assert not _is_not_enough_medals_dialog(
        frame,
        text_fn=lambda *_args, **_kwargs: ("Not enough currency", 99.0),
    )
    assert not _is_not_enough_medals_dialog(
        frame,
        text_fn=lambda *_args, **_kwargs: (
            "NOT ENOUGH MEDALS You need 240 medals to switch presets",
            60.0,
        ),
    )


def test_failed_bots_preset_dismisses_exact_medals_dialog_and_restores_home():
    router = _NoBattleRouter(
        selected=True,
        correct_guardians=True,
        deny_bots_preset=True,
    )
    router.selected[BOTS_FARM_PRESET_SLOT] = False

    with patch("core.gc_no_battle_setup._is_not_enough_medals_dialog", return_value=True):
        result = _run(router)

    assert result.status is GcNoBattleSetupStatus.FAILED
    assert result.reason == "preset did not become selected: indicators.bots:farm_slot"
    assert router.state == "home"
    assert (540, 1100) in router.static_actions
    assert router.visible_actions[-1] == "buttons.return_to_game"


def test_scoped_bots_waiver_preserves_current_preset_and_runs_later_checks():
    router = _NoBattleRouter(
        selected=True,
        correct_guardians=True,
        deny_bots_preset=True,
    )
    router.selected[BOTS_FARM_PRESET_SLOT] = False
    waiver = {
        "request_id": "gate-1",
        "decision_id": "flame",
        "value": "Flame",
    }

    result = _run(router, waivers={"bots_preset": waiver})

    assert result.complete
    assert "indicators.bots:farm_slot" not in router.visible_actions
    assert router.module_checks == [REQUIREMENTS["modules"]]
    assert result.evidence["bots_preset"] == {
        "status": "waived",
        "check_id": "bots_preset",
        "required": "Farm",
        "waiver": waiver,
    }


def test_no_battle_setup_enforces_free_upgrade_locks_after_farm_preset():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
    }
    calls = []
    lock_evidence = SimpleNamespace(
        valid=True,
        has_authoritative_mismatch=False,
        as_dict=lambda: {"valid": True, "locks": []},
    )

    def ensure_locks(lock_requirements, **kwargs):
        calls.append((lock_requirements, kwargs))
        return SimpleNamespace(
            evidence=lock_evidence,
            screenshot="workshop",
        )

    result = run_gc_no_battle_setup(
        requirements,
        screenshot="home",
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        swipe_fn=router.swipe,
        measure_selection_fn=router.measure,
        ensure_modules_fn=router.ensure_modules,
        evaluate_modules_fn=router.evaluate_modules,
        ensure_free_upgrade_locks_fn=ensure_locks,
        validate_configuration_fn=router.validate_configuration,
        sleep_fn=lambda _seconds: None,
    )

    assert result.complete
    assert len(calls) == 1
    assert calls[0][0] == list(FARM_FREE_UPGRADE_LOCKS)
    assert calls[0][1]["screenshot"] == "workshop"
    assert calls[0][1]["enforce"] is True
    assert result.evidence["free_upgrade_locks"] == {
        "valid": True,
        "locks": [],
        "boundary": "NEW_BATTLE",
        "checked": True,
        "required": list(FARM_FREE_UPGRADE_LOCKS),
        "status": "verified",
        "changed_labels": [],
    }


def test_no_battle_lock_mismatch_blocks_new_battle_boundary():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "free_upgrade_locks": list(FARM_FREE_UPGRADE_LOCKS),
    }
    lock_evidence = SimpleNamespace(
        valid=False,
        has_authoritative_mismatch=True,
        as_dict=lambda: {
            "valid": False,
            "has_authoritative_mismatch": True,
            "locks": [],
        },
    )

    result = run_gc_no_battle_setup(
        requirements,
        screenshot="home",
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        swipe_fn=router.swipe,
        measure_selection_fn=router.measure,
        ensure_modules_fn=router.ensure_modules,
        evaluate_modules_fn=router.evaluate_modules,
        ensure_free_upgrade_locks_fn=lambda *_args, **_kwargs: SimpleNamespace(
            evidence=lock_evidence,
            screenshot="workshop",
            changed_labels=(),
        ),
        validate_configuration_fn=router.validate_configuration,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcNoBattleSetupStatus.FAILED
    assert result.failed_check == "free_upgrade_locks"
    assert result.evidence["free_upgrade_locks"]["status"] == "mismatch"
    assert result.evidence["free_upgrade_locks"]["boundary"] == "NEW_BATTLE"
    assert result.evidence["free_upgrade_locks"]["valid"] is False


def test_no_battle_setup_restores_retained_bots_scroll_before_preset_check():
    router = _NoBattleRouter(
        selected=True,
        correct_guardians=True,
        bots_offscreen=True,
    )

    result = _run(router)

    assert result.complete
    assert router.swipes == ["gesture_targets.goto_top:event_bots"]


def test_no_battle_setup_observes_modules_before_battle():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        **REQUIREMENTS,
        "loadout_policies": {
            "modules": "observe",
            "target_priority": "enforce",
        },
    }

    result = _run(router, requirements)

    assert result.complete
    assert router.module_checks == []
    assert router.module_observations == [REQUIREMENTS["modules"]]
    assert "navigation.goto_modules_home" in router.static_actions
    assert result.evidence["modules"] == {
        "valid": True,
        "slots": [],
        "mode": "observe",
        "checked": True,
    }


def test_no_battle_setup_skips_preserved_modules_entirely():
    router = _NoBattleRouter(selected=True, correct_guardians=True)
    requirements = {
        key: value for key, value in REQUIREMENTS.items() if key != "modules"
    }
    requirements.pop("target_priority")
    requirements["loadout_policies"] = {
        "modules": "preserve",
        "target_priority": "preserve",
    }

    result = _run(router, requirements)

    assert result.complete
    assert router.module_checks == []
    assert "navigation.goto_modules_home" not in router.static_actions
    assert result.evidence["modules"] == {"mode": "preserve", "checked": False}


def test_no_battle_setup_rejects_unconfigured_profile_without_actions():
    router = _NoBattleRouter()
    result = run_gc_no_battle_setup(
        {**REQUIREMENTS, "workshop_preset": "Tourney"},
        screenshot="home",
        capture_fn=router.capture,
        detector=router.detect,
        detect_home_control_fn=router.home_control,
        safe_tap_fn=router.static_tap,
        tap_visible_fn=router.visible_tap,
        measure_selection_fn=router.measure,
        sleep_fn=lambda _seconds: None,
    )

    assert result.status is GcNoBattleSetupStatus.UNSUPPORTED
    assert router.static_actions == []
    assert router.visible_actions == []


def test_guardian_replacement_templates_require_known_visible_loadout():
    mismatch = cv2.imread(str(FIXTURES / "guild_guardian_gc_inactive_20260715.png"))
    correct = cv2.imread(str(FIXTURES / "guild_guardian_gc_loadout_20260713.png"))
    assert mismatch is not None
    assert correct is not None

    expected = {
        "indicators.guardian:attack_equipped": (170, 525),
        "indicators.guardian:ally_equipped": (910, 525),
        "buttons.guardian:fetch_inventory": (195, 1540),
        "buttons.guardian:summon_inventory": (540, 1540),
    }
    for label, point in expected.items():
        actual, confidence = get_match(label, screenshot=mismatch)
        assert actual == point
        assert confidence >= 0.99

    for label in (
        "indicators.guardian:attack_equipped",
        "indicators.guardian:ally_equipped",
    ):
        point, confidence = get_match(label, screenshot=correct)
        assert point is None
        assert confidence < 0.9


def test_app_runs_no_battle_setup_before_starting_profile_battle():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"cards_deck": "Farm"},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    run_setup.assert_called_once_with(
        REQUIREMENTS,
        screenshot=frame,
        action_guard_fn=app._runtime_action_guard,
    )
    manager.mark_no_battle_setup_complete.assert_called_once_with(setup.evidence)
    handle_home.assert_called_once_with(restart_enabled=True)
    manager.on_home.assert_called_once_with()


def test_app_binds_save_preflight_to_only_an_exact_new_battle_launch():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    manager.strategy.runtime_policy.return_value = {
        "player_save_preflight": "save_first"
    }
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    app._runtime_action_guard = Mock(return_value=True)
    app._player_save_preflight_session_id = ""
    app._player_save_preflight_result = None
    preflight = SimpleNamespace(
        ready=True,
        decisions={
            "cards_deck": {
                "disposition": "save_match",
                "expected": "Farm",
                "observed": "Farm",
            }
        },
        as_dict=lambda: {"status": "ready"},
    )
    coordinator = Mock()
    coordinator.acquire.return_value = preflight
    coordinator.carry = SimpleNamespace(
        state=CarriedEvidenceState.PENDING_LAUNCH
    )
    app._player_save_preflight_coordinator = coordinator
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"cards_deck": {"source": "player_save_preflight"}},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch(
            "core.app.handle_home_screen",
            return_value=True,
        ) as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    coordinator.acquire.assert_called_once_with(
        REQUIREMENTS,
        mode="save_first",
        initial_frame=frame,
    )
    run_setup.assert_called_once_with(
        REQUIREMENTS,
        screenshot=frame,
        action_guard_fn=app._runtime_action_guard,
        save_decisions=preflight.decisions,
        snapshot_invalidation_fn=coordinator.invalidate,
        save_ui_verification_fn=coordinator.record_ui_verification,
    )
    handle_home.assert_called_once_with(
        restart_enabled=True,
        require_new_battle=True,
    )
    coordinator.mark_runtime_launch.assert_called_once_with(
        control=HomeBattleControl.NEW_BATTLE,
        action_authorized=True,
        dispatched=True,
    )
    manager.mark_no_battle_setup_complete.assert_called_once_with(
        {
            **setup.evidence,
            "player_save_preflight": {"status": "ready"},
        }
    )


def test_app_feeds_history_from_the_same_home_preflight_result():
    manager = Mock()
    manager.strategy.runtime_policy.return_value = {
        "player_save_preflight": "save_first"
    }
    result = SimpleNamespace(
        ready=True,
        history_tail={"disposition": "save_match"},
        history_scope_id="scope-1",
    )
    coordinator = Mock()
    coordinator.acquire.return_value = result
    continuity = Mock()
    continuity.accept_home_save_baseline.return_value = SimpleNamespace(
        accepted=True
    )
    app = App.__new__(App)
    app._mission_mgr = manager
    app._player_save_preflight_coordinator = coordinator
    app._player_save_preflight_session_id = ""
    app._activity_continuity = continuity

    with patch(
        "core.app.get_activity_scope",
        return_value={"run_id": "scope-1"},
    ):
        observed = app._acquire_player_save_home_preflight(
            REQUIREMENTS,
            screenshot="home",
        )

    assert observed is result
    coordinator.acquire.assert_called_once_with(
        REQUIREMENTS,
        mode="save_first",
        initial_frame="home",
    )
    continuity.accept_home_save_baseline.assert_called_once_with(
        result.history_tail,
        expected_scope_id="scope-1",
        player_save_mode="save_first",
    )
    assert app._player_save_preflight_activity_scope_id == "scope-1"


def test_baseline_only_preflight_context_does_not_require_a_strategy():
    app = App.__new__(App)
    app._adb_target_session = SimpleNamespace(
        snapshot=lambda: AdbTargetSnapshot("private-target", 3, True)
    )
    app._mission_mgr = SimpleNamespace(strategy=None)
    app._player_save_preflight_session_id = "preflight-1"
    app._player_save_runtime_session_id = "runtime-1"

    with patch(
        "core.app.get_activity_scope",
        return_value={"run_id": "scope-1"},
    ):
        context = app._current_player_save_preflight_context()

    assert context.strategy_name == "none"
    assert context.configuration_fingerprint == "history-baseline-only"
    assert context.activity_scope_id == "scope-1"


def test_app_does_not_repeat_save_or_navigate_when_history_scope_is_blocked():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    manager.strategy.runtime_policy.return_value = {
        "player_save_preflight": "save_first"
    }
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    app._player_save_preflight_activity_scope_id = "scope-1"
    app._player_save_history_baseline_outcome = SimpleNamespace(blocked=True)
    coordinator = Mock()
    app._player_save_preflight_coordinator = coordinator

    with (
        patch("core.app.get_activity_scope", return_value={"run_id": "scope-1"}),
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup") as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    coordinator.acquire.assert_not_called()
    run_setup.assert_not_called()
    handle_home.assert_not_called()


def test_save_first_home_without_requirements_forces_one_baseline_bundle():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = {}
    manager.strategy.runtime_policy.return_value = {
        "player_save_preflight": "save_first",
        "handlers": [],
    }
    app = App.__new__(App)
    app._auto_start_enabled = False
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    app._player_save_preflight_activity_scope_id = None
    app._player_save_preflight_result = None
    app._player_save_history_baseline_outcome = None
    result = SimpleNamespace(
        ready=True,
        history_tail={"disposition": "save_match"},
        history_scope_id="scope-1",
        decisions={},
    )
    coordinator = Mock()
    coordinator.acquire.return_value = result
    coordinator.carry = None
    app._player_save_preflight_coordinator = coordinator
    continuity = Mock()
    continuity.accept_home_save_baseline.return_value = SimpleNamespace(
        accepted=True,
        blocked=False,
        ui_required=False,
    )
    app._activity_continuity = continuity

    with (
        patch(
            "core.app.get_activity_scope",
            return_value={"run_id": "scope-1"},
        ),
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch.object(app, "_handler_enabled", return_value=False),
        patch.object(app, "_exclusive_validation_definition", return_value=None),
        patch.object(app, "_report_home_policy"),
        patch("core.app.run_gc_no_battle_setup") as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    coordinator.acquire.assert_called_once_with(
        {},
        mode="save_first",
        initial_frame=frame,
    )
    continuity.accept_home_save_baseline.assert_called_once()
    run_setup.assert_not_called()
    handle_home.assert_not_called()


def test_app_runs_tournament_home_preflight_without_starting_battle():
    frame = object()
    manager = Mock()
    manager.strategy = Mock()
    manager.strategy.runtime_policy.return_value = {
        "handlers": ["ad_gem", "game_over"],
        "home_preflight": True,
    }
    manager.no_battle_setup_requirements.return_value = TOURNAMENT_REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = False
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"cards_deck": "Tournament"},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    run_setup.assert_called_once_with(
        TOURNAMENT_REQUIREMENTS,
        screenshot=frame,
        action_guard_fn=app._runtime_action_guard,
    )
    manager.mark_no_battle_setup_complete.assert_called_once_with(setup.evidence)
    handle_home.assert_not_called()
    manager.on_home.assert_not_called()


def test_tournament_home_policy_reports_changed_readiness_without_heartbeat():
    strategy = get_strategy("tournament")
    assert strategy is not None
    receipt = {
        "request_id": "validation-request",
        "strategy_request_id": "strategy-request",
        "status": "pending",
    }
    manager = SimpleNamespace(
        strategy=strategy,
        ctx=SimpleNamespace(
            data={
                "mission_vars": {
                    "gc_session_preflight_completed": False,
                }
            }
        ),
    )
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = SimpleNamespace(
        strategy_request=("tournament", "strategy-request", "next_boundary"),
        exclusive_validation_receipt=lambda **_kwargs: dict(receipt),
        is_paused=False,
    )
    app._last_home_policy_signature = None

    with patch("core.app.log") as emit:
        for _ in range(2):
            app._report_home_policy(
                home_control=HomeBattleControl.NEW_BATTLE,
                home_handler_enabled=False,
                home_preflight_enabled=True,
                requirements_pending=False,
            )

        assert emit.call_count == 1
        assert "authorized ordinary validation battle is pending" in (
            emit.call_args.args[0]
        )

        receipt.update(
            status="result",
            outcome="ready",
            reason="checks passed",
            launch={"status": "awaiting_operator"},
        )
        app._report_home_policy(
            home_control=HomeBattleControl.NEW_BATTLE,
            home_handler_enabled=False,
            home_preflight_enabled=True,
            requirements_pending=False,
        )

    assert emit.call_count == 2
    assert emit.call_args.args[0].startswith("[TOURNAMENT_READY]")
    assert "waiting for operator confirmation" in emit.call_args.args[0]


def test_normal_home_policy_reports_disposition_changes_without_heartbeat():
    app = App.__new__(App)
    app._mission_mgr = SimpleNamespace(ctx=SimpleNamespace(data={}))
    app._current_strategy_name = Mock(return_value="farm_t19")
    app._exclusive_validation_receipt = Mock(return_value=None)
    app._exclusive_validation_definition = Mock(return_value=None)
    app._last_home_policy_signature = None
    original_mode = AUTOMATION.mode

    try:
        with patch("core.app.log") as emit:
            AUTOMATION.mode = ExecMode.HOME
            for _ in range(2):
                app._report_home_policy(
                    home_control=HomeBattleControl.NEW_BATTLE,
                    home_handler_enabled=True,
                    home_preflight_enabled=True,
                    requirements_pending=False,
                )

            assert emit.call_count == 1
            assert "Stay Home active" in emit.call_args.args[0]

            AUTOMATION.mode = ExecMode.WAIT
            app._report_home_policy(
                home_control=HomeBattleControl.NEW_BATTLE,
                home_handler_enabled=True,
                home_preflight_enabled=True,
                requirements_pending=False,
            )

        assert emit.call_count == 2
        assert "Wait active" in emit.call_args.args[0]
    finally:
        AUTOMATION.mode = original_mode


def test_app_blocks_battle_start_when_no_battle_setup_fails():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._capture_frame = Mock(return_value=frame)
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(GcNoBattleSetupStatus.FAILED, "mismatch")

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch(
            "core.app.run_gc_no_battle_setup",
            return_value=setup,
        ) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    assert run_setup.call_count == HOME_SETUP_MAX_ATTEMPTS
    assert app._capture_frame.call_count == HOME_SETUP_MAX_ATTEMPTS - 1
    manager.mark_no_battle_setup_complete.assert_not_called()
    handle_home.assert_not_called()
    manager.on_home.assert_not_called()


def test_app_does_not_restart_home_setup_after_local_repair_exhaustion():
    frame = object()
    app = App.__new__(App)
    app._runtime_action_guard = Mock(return_value=True)
    app._capture_frame = Mock()
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.FAILED,
        "Ban Perks made no stable progress",
        failed_check="perk_configuration",
        retryable_from_home=False,
    )

    with patch(
        "core.app.run_gc_no_battle_setup",
        return_value=setup,
    ) as run_setup:
        result = app._run_home_setup_attempts(
            REQUIREMENTS,
            screenshot=frame,
        )

    assert result is setup
    run_setup.assert_called_once()
    app._capture_frame.assert_not_called()


def test_app_retries_transient_home_setup_failure_before_starting_battle():
    frame = object()
    retry_frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._capture_frame = Mock(return_value=retry_frame)
    app._publish_gate_decision = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    failed = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.FAILED,
        "transient cards evidence mismatch",
        failed_check="cards_deck",
    )
    completed = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"cards_deck": "Farm"},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch(
            "core.app.run_gc_no_battle_setup",
            side_effect=[failed, completed],
        ) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    assert run_setup.call_args_list == [
        call(
            REQUIREMENTS,
            screenshot=frame,
            action_guard_fn=app._runtime_action_guard,
        ),
        call(
            REQUIREMENTS,
            screenshot=retry_frame,
            action_guard_fn=app._runtime_action_guard,
        ),
    ]
    app._publish_gate_decision.assert_not_called()
    manager.mark_no_battle_setup_complete.assert_called_once_with(
        completed.evidence
    )
    handle_home.assert_called_once_with(restart_enabled=True)


def test_app_does_not_publish_gate_or_start_after_control_interruption():
    frame = object()
    manager = Mock()
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._supervisor = Mock()
    app._publish_gate_decision = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.INTERRUPTED,
        "automation paused during Home setup",
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch("core.app.run_gc_no_battle_setup", return_value=setup),
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    app._publish_gate_decision.assert_not_called()
    manager.mark_no_battle_setup_complete.assert_not_called()
    handle_home.assert_not_called()
    manager.on_home.assert_not_called()


def test_app_configured_fallback_waives_only_failed_check_and_retries_setup():
    frame = object()
    recovered_home = object()
    manager = Mock()
    manager.strategy = SimpleNamespace(name="farm_t18")
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    manager.gate_fallbacks.return_value = [
        {
            "id": "flame",
            "label": "Continue with Flame for this run",
            "value": "Flame",
        }
    ]
    supervisor = Mock()
    supervisor.gate_decision = None
    pending = {
        "request_id": "gate-1",
        "status": "pending",
        "strategy": "farm_t18",
        "phase": "home_setup",
        "check_id": "bots_preset",
        "reason": "preset did not become selected",
        "options": [
            {
                "id": "flame",
                "label": "Continue with Flame for this run",
                "value": "Flame",
                "kind": "fallback",
                "action": "waive",
            }
        ],
    }
    resolved = {
        **pending,
        "status": "resolved",
        "decision_id": "flame",
        "selected_option": pending["options"][0],
    }
    supervisor.publish_gate_decision.return_value = pending
    supervisor.resolve_gate_decision.return_value = resolved
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._supervisor = supervisor
    app._gate_decision_prompt = lambda _decision: "flame"
    app._gate_prompted_request_id = None
    app._startup_gate_waivers = {}
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    app._capture_frame = Mock(return_value=recovered_home)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.FAILED,
        "preset did not become selected: indicators.bots:farm_slot",
        {"cards_deck": "Farm", "workshop_preset": "Farm"},
        "bots_preset",
    )
    completed = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"bots_preset": {"status": "waived"}},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch(
            "core.app.run_gc_no_battle_setup",
            side_effect=[setup, setup, setup, completed],
        ) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    supervisor.publish_gate_decision.assert_called_once()
    supervisor.resolve_gate_decision.assert_called_once_with(
        "gate-1",
        "flame",
        source="runtime-cli",
    )
    waiver = {
        "request_id": "gate-1",
        "decision_id": "flame",
        "label": "Continue with Flame for this run",
        "kind": "fallback",
        "value": "Flame",
        "reason": "preset did not become selected",
    }
    assert run_setup.call_args_list == [
        call(
            REQUIREMENTS,
            screenshot=frame,
            action_guard_fn=app._runtime_action_guard,
        ),
        call(
            REQUIREMENTS,
            screenshot=recovered_home,
            action_guard_fn=app._runtime_action_guard,
        ),
        call(
            REQUIREMENTS,
            screenshot=recovered_home,
            action_guard_fn=app._runtime_action_guard,
        ),
        call(
            REQUIREMENTS,
            screenshot=recovered_home,
            action_guard_fn=app._runtime_action_guard,
            waivers={"bots_preset": waiver},
        ),
    ]
    manager.mark_no_battle_setup_complete.assert_called_once_with(
        completed.evidence,
        waivers={"bots_preset": waiver},
    )
    supervisor.consume_gate_decision.assert_called_once_with(
        "gate-1",
        completion_reason="waived bots_preset for this run",
    )
    handle_home.assert_called_once_with(restart_enabled=True)
    manager.on_home.assert_called_once_with()


def test_app_claims_optional_configured_skip_before_home_setup():
    frame = object()
    manager = Mock()
    manager.strategy = SimpleNamespace(name="farm_t18")
    manager.no_battle_setup_requirements.return_value = REQUIREMENTS
    supervisor = Mock()
    supervisor.gate_decision = None
    supervisor.claim_startup_gate_waivers.return_value = {
        "bots_preset": {
            "request_id": "proactive-1",
            "check_id": "bots_preset",
            "label": "Bot preset",
            "status": "claimed",
            "strategy": "farm_t18",
        }
    }
    app = App.__new__(App)
    app._auto_start_enabled = True
    app._mission_mgr = manager
    app._supervisor = supervisor
    app._startup_gate_waivers = {}
    app._fast_game_over = False
    app._last_wave_value = None
    app._last_wave_conf = -1.0
    app._status_reporter = Mock()
    app._handle_daily_gem_if_due = Mock(return_value=False)
    app._handle_mission_rewards_if_due = Mock(return_value=False)
    setup = GcNoBattleSetupResult(
        GcNoBattleSetupStatus.COMPLETE,
        "ok",
        {"bots_preset": {"status": "waived"}},
    )

    with (
        patch(
            "core.app.detect_home_battle_control",
            return_value=HomeBattleEvidence(
                HomeBattleControl.NEW_BATTLE,
                "test",
                100.0,
            ),
        ),
        patch(
            "core.app.run_gc_no_battle_setup",
            return_value=setup,
        ) as run_setup,
        patch("core.app.handle_home_screen") as handle_home,
    ):
        app._handle_primary_states("HOME_SCREEN", set(), frame)

    waiver = {
        "request_id": "proactive-1",
        "decision_id": "proactive_skip",
        "label": "Bot preset",
        "kind": "proactive",
        "value": "",
        "reason": "configured before the run",
    }
    run_setup.assert_called_once_with(
        REQUIREMENTS,
        screenshot=frame,
        action_guard_fn=app._runtime_action_guard,
        waivers={"bots_preset": waiver},
    )
    manager.mark_no_battle_setup_complete.assert_called_once_with(
        setup.evidence,
        waivers={"bots_preset": waiver},
    )
    handle_home.assert_called_once_with(restart_enabled=True)


def test_app_claims_optional_session_only_skip_for_active_run():
    manager = Mock()
    manager.strategy = SimpleNamespace(
        name="farm_t18",
        session_preflight_requirements=lambda: {
            "bots_preset": "Farm",
            "auto_pick_perks": True,
        },
    )
    supervisor = Mock()
    supervisor.gate_decision = None
    supervisor.claim_startup_gate_waivers.return_value = {
        "auto_pick_perks": {
            "request_id": "proactive-auto",
            "check_id": "auto_pick_perks",
            "label": "Auto Pick Perks",
            "status": "claimed",
            "strategy": "farm_t18",
        }
    }
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = supervisor
    app._startup_gate_waivers = {}

    applied = app._claim_proactive_gate_waivers(for_home_setup=False)

    assert set(applied) == {"auto_pick_perks"}
    manager.waive_session_preflight_check.assert_called_once_with(
        "auto_pick_perks",
        applied["auto_pick_perks"],
    )
    manager.waive_session_preflight_check.assert_called_once()


def test_terminal_session_bypass_rearms_only_the_failed_auto_pick_check():
    manager = Mock()
    manager.strategy = SimpleNamespace(
        name="farm_t18",
        session_preflight_requirements=lambda: {"auto_pick_perks": True},
    )
    manager.session_preflight_failure_checks.return_value = ["auto_pick_perks"]
    manager.gate_fallbacks.return_value = []
    manager.ctx.data = {
        "mission_vars": {
            "gc_session_preflight_last_reason": "configuration mismatch",
        }
    }
    options = build_gate_decision_options("auto_pick_perks")
    pending = {
        "request_id": "gate-auto-pick",
        "status": "pending",
        "strategy": "farm_t18",
        "phase": "session_preflight",
        "check_id": "auto_pick_perks",
        "reason": "configuration mismatch",
        "expected": "True",
        "options": options,
    }
    resolved = {
        **pending,
        "status": "resolved",
        "decision_id": "bypass_once",
        "selected_option": next(
            option for option in options if option["id"] == "bypass_once"
        ),
    }
    supervisor = Mock()
    supervisor.gate_decision = None
    supervisor.publish_gate_decision.return_value = pending
    supervisor.resolve_gate_decision.return_value = resolved
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = supervisor
    app._gate_decision_prompt = lambda _decision: "bypass_once"
    app._gate_prompted_request_id = None

    app._handle_terminal_session_gate_decision()

    manager.waive_session_preflight_check.assert_called_once()
    check_id, waiver = manager.waive_session_preflight_check.call_args.args
    assert check_id == "auto_pick_perks"
    assert waiver["decision_id"] == "bypass_once"
    supervisor.consume_gate_decision.assert_called_once_with(
        "gate-auto-pick",
        completion_reason="waived auto_pick_perks for this run",
    )


def test_attached_session_mismatch_restarts_only_after_operator_authorization():
    repair_authority = {
        "game_state": "active_battle",
        "runtime_id": "runtime-1",
        "pid": 1234,
        "adb_target": "localhost:5555",
        "target_generation": 1,
        "activity_scope_run_id": "run-1",
    }
    manager = Mock()
    manager.strategy = SimpleNamespace(
        name="farm_t18",
        session_preflight_requirements=lambda: {"modules": {"cannon": "Farm"}},
    )
    manager.session_preflight_failure_checks.return_value = ["modules"]
    manager.session_preflight_restart_available.return_value = True
    manager.authorize_session_preflight_restart.return_value = True
    manager.active_battle_observed.return_value = True
    manager.gate_fallbacks.return_value = []
    manager.ctx.data = {
        "mission_vars": {
            "gc_session_preflight_last_reason": "configuration mismatch",
        }
    }
    options = build_gate_decision_options(
        "modules",
        allow_repair_restart=True,
    )
    pending = {
        "request_id": "gate-attached-modules",
        "status": "pending",
        "strategy": "farm_t18",
        "phase": "session_preflight",
        "check_id": "modules",
        "reason": "configuration mismatch",
        "expected": {"cannon": "Farm"},
        "options": options,
        "repair_authority": repair_authority,
    }
    resolved = {
        **pending,
        "status": "resolved",
        "decision_id": "restart_and_repair",
        "selected_option": next(
            option for option in options
            if option["id"] == "restart_and_repair"
        ),
    }
    supervisor = Mock()
    supervisor.gate_decision = None
    supervisor.publish_gate_decision.return_value = pending
    supervisor.resolve_gate_decision.return_value = resolved
    app = App.__new__(App)
    app._mission_mgr = manager
    app._supervisor = supervisor
    app._gate_decision_prompt = lambda _decision: "restart_and_repair"
    app._gate_prompted_request_id = None
    app._current_control_workflow_evidence = lambda: repair_authority

    app._handle_terminal_session_gate_decision()

    manager.authorize_session_preflight_restart.assert_called_once_with(
        repair_authority,
        request_id="gate-attached-modules",
        check_id="modules",
        reason="configuration mismatch",
    )
    supervisor.consume_gate_decision.assert_called_once_with(
        "gate-attached-modules",
        completion_reason=(
            "authorized guarded battle restart to repair modules"
        ),
    )
