from types import SimpleNamespace
from unittest.mock import patch

from automation.missions.base import MissionContext
from core.action_executor import _bind_save_backed_home_evidence, execute_actions
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight import _free_upgrade_lock_boundary_evidence


ORDER = [
    "Fast",
    "Protector",
    "Fleets",
    "Boss",
    "Elites",
    "In Spotlight",
    "Tank",
    "Closest (Default)",
    "Ranged",
    "Basic",
]
MODULES = {
    "cannon_primary": "Amplifying Strike",
    "armor_primary": "Orbital Augment",
    "generator_primary": "Black Hole Digestor",
    "core_primary": "Multiverse Nexus",
    "cannon_assist": "Being Annihilator",
    "armor_assist": "Anti-Cube Portal",
    "generator_assist": "Singularity Harness",
    "core_assist": "Dimension Core",
}
TOURNAMENT_REFERENCE = {
    **MODULES,
    "generator_primary": "Project Funding",
    "core_primary": "Dimension Core",
    "core_assist": "Harmony Conductor",
}
TOURNAMENT_VARIATION = {
    **TOURNAMENT_REFERENCE,
    "armor_primary": "Anti-Cube Portal",
    "armor_assist": "Space Displacer",
}


def test_target_priority_consumes_bound_exact_order_without_opening_ui():
    class BoundSave:
        def consume(self, check_id):
            assert check_id == "target_priority"
            return list(ORDER)

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )

    with patch(
        "core.action_executor.ensure_target_priority_order"
    ) as ensure:
        execute_actions(
            None,
            [
                {
                    "type": "target_priority_ensure",
                    "order": list(ORDER),
                    "_strategy": True,
                }
            ],
            ctx,
            action_guard_fn=lambda: True,
        )

    ensure.assert_not_called()
    assert ctx.data["mission_vars"]["target_priority_checked"] is True
    assert ctx.data["mission_vars"]["target_priority_evidence"] == {
        "source": "bound_player_save_preflight",
        "checked": False,
        "valid": True,
        "order": ORDER,
    }


def test_target_priority_carried_mismatch_invalidates_then_runs_existing_ui():
    invalidations = []

    class BoundSave:
        def consume(self, _check_id):
            return list(reversed(ORDER))

        def invalidate(self, reason):
            invalidations.append(reason)

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )

    with patch(
        "core.action_executor.ensure_target_priority_order",
        return_value=True,
    ) as ensure:
        execute_actions(
            None,
            [{"type": "target_priority_ensure", "order": list(ORDER)}],
            ctx,
            action_guard_fn=lambda: True,
        )

    assert invalidations == ["target_priority_action_requirement_changed"]
    ensure.assert_called_once()
    assert ensure.call_args.kwargs["expected"] == ORDER
    assert callable(ensure.call_args.kwargs["repair_observer_fn"])


def test_target_priority_ui_repair_invalidates_other_carried_checks():
    invalidations = []

    class BoundSave:
        def consume(self, _check_id):
            return None

        def invalidate(self, reason):
            invalidations.append(reason)

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )

    def ensure(**kwargs):
        kwargs["repair_observer_fn"]()
        return True

    with patch(
        "core.action_executor.ensure_target_priority_order",
        side_effect=ensure,
    ):
        execute_actions(
            None,
            [{"type": "target_priority_ensure", "order": list(ORDER)}],
            ctx,
            action_guard_fn=lambda: True,
        )

    assert invalidations == ["in_battle_target_priority_repair"]


def test_bound_save_locks_preserve_required_subset_with_unmanaged_extra():
    expected = list(FARM_FREE_UPGRADE_LOCKS)

    class BoundSave:
        def consume(self, check_id):
            assert check_id == "free_upgrade_locks"
            return [*expected, "Health"]

    setup = {
        "free_upgrade_locks": {
            "status": "save_match",
            "source": "player_save_preflight",
            "checked": False,
            "boundary": "NEW_BATTLE",
            "valid": True,
            "required": list(expected),
            "observed": list(expected),
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())
    normalized = _free_upgrade_lock_boundary_evidence(
        tuple(expected),
        bound["free_upgrade_locks"],
    )

    assert normalized["source"] == "bound_player_save_preflight"
    assert normalized["blocking_valid"] is True
    assert normalized["valid"] is True
    assert normalized["observed"] == [*expected, "Health"]
    assert normalized["diagnostics"]["unmanaged_locks"] == ["Health"]


def test_exact_save_backed_modules_bind_into_final_session_evidence():
    class BoundSave:
        def consume(self, check_id):
            assert check_id == "modules"
            return dict(MODULES)

    setup = {
        "modules": {
            "status": "save_match",
            "source": "player_save_preflight",
            "checked": False,
            "valid": True,
            "slots": [
                {
                    "slot_key": key,
                    "expected": value,
                    "actual": value,
                    "valid": True,
                }
                for key, value in MODULES.items()
            ],
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())

    assert bound["modules"]["source"] == "bound_player_save_preflight"
    assert bound["modules"]["valid"] is True
    assert {
        slot["slot_key"]: slot["actual"]
        for slot in bound["modules"]["slots"]
    } == MODULES


def test_observed_module_variation_binds_without_enforcement():
    class BoundSave:
        def consume(self, check_id):
            assert check_id == "modules"
            return dict(TOURNAMENT_VARIATION)

    setup = {
        "modules": {
            "status": "save_observation",
            "source": "player_save_preflight",
            "checked": False,
            "mode": "observe",
            "valid": False,
            "fully_observed": True,
            "slots": [
                {
                    "slot_key": key,
                    "expected": value,
                    "actual": TOURNAMENT_VARIATION[key],
                    "valid": TOURNAMENT_VARIATION[key] == value,
                }
                for key, value in TOURNAMENT_REFERENCE.items()
            ],
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())

    assert bound["modules"]["source"] == "bound_player_save_preflight"
    assert bound["modules"]["mode"] == "observe"
    assert bound["modules"]["valid"] is False
    assert bound["modules"]["fully_observed"] is True
    assert {
        slot["slot_key"]: slot["actual"]
        for slot in bound["modules"]["slots"]
    } == TOURNAMENT_VARIATION


def test_changed_observed_module_carry_invalidates_before_session_use():
    invalidations = []

    class BoundSave:
        def consume(self, _check_id):
            return {
                **TOURNAMENT_VARIATION,
                "armor_assist": "Anti-Cube Portal",
            }

        def invalidate(self, reason):
            invalidations.append(reason)

    setup = {
        "modules": {
            "status": "save_observation",
            "source": "player_save_preflight",
            "mode": "observe",
            "slots": [
                {
                    "slot_key": key,
                    "expected": value,
                    "actual": TOURNAMENT_VARIATION[key],
                }
                for key, value in TOURNAMENT_REFERENCE.items()
            ],
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())

    assert "modules" not in bound
    assert invalidations == ["module_boundary_requirement_changed"]


def test_changed_later_lock_requirement_invalidates_carried_snapshot():
    invalidations = []

    class BoundSave:
        def consume(self, check_id):
            assert check_id == "free_upgrade_locks"
            return list(FARM_FREE_UPGRADE_LOCKS)

        def invalidate(self, reason):
            invalidations.append(reason)

    setup = {
        "free_upgrade_locks": {
            "status": "save_match",
            "source": "player_save_preflight",
            "required": ["different lock"],
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())

    assert "free_upgrade_locks" not in bound
    assert invalidations == [
        "free_upgrade_lock_boundary_requirement_changed"
    ]


def test_unbound_home_uw_copy_is_not_reused_by_session_preflight():
    setup = {
        "ultimate_weapons": {
            "source": "player_save_preflight",
            "observations": {"Poison Swamp": {"stun": "off"}},
        }
    }

    bound = _bind_save_backed_home_evidence(setup, object())

    assert "ultimate_weapons" not in bound


def test_ui_only_configuration_repairs_invalidate_remaining_carried_checks():
    invalidations = []

    class BoundSave:
        def invalidate(self, reason):
            invalidations.append(reason)

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )
    damage = SimpleNamespace(
        changed=True,
        expected="100",
        initial="90",
        final="100",
        steps=1,
        success=True,
        reason="verified",
        as_dict=lambda: {"changed": True},
    )
    orb = SimpleNamespace(
        changed=True,
        range_observed="30.00",
        range_basis="active",
        expected_extra="30.00",
        expected_workshop="39.00",
        initial_extra="31.00",
        initial_workshop="39.00",
        final_extra="30.00",
        final_workshop="39.00",
        extra_steps=1,
        workshop_steps=0,
        success=True,
        reason="verified",
        as_dict=lambda: {"changed": True},
    )

    with (
        patch(
            "core.action_executor.configure_damage_slider",
            return_value=damage,
        ),
        patch(
            "core.action_executor.configure_orb_distance",
            return_value=orb,
        ),
    ):
        execute_actions(
            None,
            [
                {
                    "type": "damage_slider_configure",
                    "mode": "enforce",
                    "value": "100",
                },
                {
                    "type": "orb_distance_configure",
                    "mode": "enforce",
                    "range_basis": "active",
                    "extra": "30.00",
                    "workshop": "39.00",
                },
            ],
            ctx,
            action_guard_fn=lambda: True,
        )

    assert invalidations == [
        "in_battle_damage_slider_repair",
        "in_battle_orb_distance_repair",
    ]
