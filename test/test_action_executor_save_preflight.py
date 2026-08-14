from types import SimpleNamespace
from unittest.mock import patch

from automation.missions.base import MissionContext
from core.action_executor import _bind_save_backed_home_evidence, execute_actions
from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from core.gc_preflight import _free_upgrade_lock_boundary_evidence
from core.gc_preflight_navigation import (
    GcLivePreflightResult,
    GcPreflightNavigationStatus,
)


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


def test_running_attachment_always_uses_in_battle_preflight_route():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {"valid": True},
        deferred_checks=(),
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "all requirements verified",
        evidence,
    )

    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        return_value=result,
    ) as run_preflight:
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "_strategy": True,
                }
            ],
            ctx,
        )

    run_preflight.assert_called_once_with(
        {"cards_deck": "Farm"},
        stay_in_battle=True,
        allow_repair=False,
    )


def test_running_attachment_mismatch_completes_degraded_without_home_repair():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {
            "valid": False,
            "failed_checks": ["modules"],
        },
        failed_checks=("modules",),
        requires_no_battle_repair=True,
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.MISMATCH,
        "configuration mismatch",
        evidence,
    )

    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        return_value=result,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "allow_repair": True,
                    "_strategy": True,
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"] is True
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_blocked"] is False
    assert variables["gc_session_preflight_repair_required"] is False
    assert variables["gc_session_preflight_restart_available"] is False


def test_preflight_exception_completes_degraded_and_releases_the_gate():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )

    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        side_effect=RuntimeError("screen reader unavailable"),
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "_strategy": True,
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"] is True
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_blocked"] is False
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_failed_checks"] == [
        "session_preflight"
    ]


def test_preflight_setup_exception_after_attempt_releases_the_gate():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )

    with patch(
        "core.action_executor.merge_profile_skip_waivers",
        side_effect=RuntimeError("waiver state unavailable"),
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "_strategy": True,
                    "_attachment_validation": True,
                    "_attachment_rule_id": "attached_preflight",
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_attempted"] is True
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"


def test_unknown_attached_validation_action_is_suppressed_once():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )

    with patch("core.action_executor.ensure_ultimate_state") as ensure_ultimate:
        execute_actions(
            object(),
            [
                {
                    "type": "ultimate_ensure_state",
                    "targets": ["spotlight"],
                    "_strategy": True,
                    "_attachment_validation": True,
                    "_attachment_rule_id": "unsafe_attached_rule",
                }
            ],
            ctx,
        )

    ensure_ultimate.assert_not_called()
    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_failed_checks"] == [
        "attached_action_ultimate_ensure_state"
    ]
    assert variables["attached_validation_rule_dispositions"] == {
        "unsafe_attached_rule": {
            "action": "ultimate_ensure_state",
            "disposition": "suppressed_degraded",
        }
    }


def test_attached_target_priority_exception_completes_degraded_once():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )

    with patch(
        "core.action_executor.observe_target_priority_order",
        side_effect=RuntimeError("priority panel unreadable"),
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "target_priority_ensure",
                    "order": ORDER,
                    "_strategy": True,
                    "_attachment_validation": True,
                    "_attachment_rule_id": "attached_target_priority",
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["target_priority_checked"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_failed_checks"] == [
        "target_priority"
    ]
    assert variables["attached_validation_rule_dispositions"] == {
        "attached_target_priority": {
            "action": "target_priority_observe",
            "disposition": "observer_failed_degraded",
        }
    }


def test_malformed_preflight_completes_degraded_instead_of_stranding_attach():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )

    execute_actions(
        object(),
        [{"type": "gc_session_preflight", "_strategy": True}],
        ctx,
    )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"


def test_attachment_retains_mismatches_and_unverified_deferrals():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {
            "valid": True,
            "reported_attachment_mismatches": {
                "workshop_preset": {"disposition": "save_mismatch"}
            },
            "deferred_checks": ["damage_slider"],
        }
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "active requirements checked",
        evidence,
    )

    with (
        patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ),
        patch("core.action_executor.log_mission") as mission_log,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"cards_deck": "Farm"},
                    "_strategy": True,
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_failed_checks"] == [
        "damage_slider",
        "workshop_preset",
    ]
    mission_log.assert_any_call(
        "[SESSION_PREFLIGHT] Attachment validation flagged configuration "
        "gaps; Automation continues degraded and repair is deferred to Home",
        "WARN",
    )


def test_attachment_home_only_deferral_is_not_a_configuration_mismatch():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {
            "valid": True,
            "reported_attachment_mismatches": {},
            "deferred_checks": ["free_upgrade_locks"],
        }
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "active requirements verified; boundary checks deferred",
        evidence,
    )

    with (
        patch(
            "core.action_executor.run_read_only_gc_preflight",
            return_value=result,
        ),
        patch("core.action_executor.log_mission") as mission_log,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {"free_upgrade_locks": ["Shockwave Size"]},
                    "_strategy": True,
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_failed_checks"] == [
        "free_upgrade_locks"
    ]
    assert any(
        "could not verify Home-only checks" in call.args[0]
        for call in mission_log.call_args_list
    )


def test_manual_return_keeps_deferred_checks_unresolved_and_degraded():
    ctx = MissionContext(
        data={
            "manual_return_reconciliation_active": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
        }
    )
    evidence = SimpleNamespace(
        as_dict=lambda: {
            "valid": True,
            "reported_attachment_mismatches": {},
            "deferred_checks": ["free_upgrade_locks"],
        }
    )
    result = GcLivePreflightResult(
        GcPreflightNavigationStatus.COMPLETE,
        "active requirements checked; Home-only evidence deferred",
        evidence,
    )

    with patch(
        "core.action_executor.run_read_only_gc_preflight",
        return_value=result,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "gc_session_preflight",
                    "requirements": {
                        "free_upgrade_locks": ["Shockwave Size"]
                    },
                    "_strategy": True,
                }
            ],
            ctx,
        )

    variables = ctx.data["mission_vars"]
    assert variables["gc_session_preflight_completed"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_failed_checks"] == [
        "free_upgrade_locks"
    ]


def test_attached_battle_controls_are_observed_without_repair_and_complete_degraded():
    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {
                "last_detection_state": "RUNNING",
                "gc_session_preflight_completed": True,
                "gc_session_preflight_failed_checks": [],
            },
        }
    )
    damage = SimpleNamespace(
        expected="100",
        initial="90",
        final="90",
        steps=0,
        success=False,
        reason="observed_mismatch",
        as_dict=lambda: {
            "mode": "observe",
            "observed": True,
            "matches": False,
            "changed": False,
            "dismissed": True,
        },
    )
    orb = SimpleNamespace(
        range_observed="98.38m",
        range_basis="98.38m",
        expected_extra="87.16m",
        expected_workshop="80.37m",
        initial_extra=None,
        initial_workshop=None,
        final_extra=None,
        final_workshop=None,
        extra_steps=0,
        workshop_steps=0,
        success=False,
        reason="panel_not_verified",
        as_dict=lambda: {
            "mode": "observe",
            "observed": False,
            "matches": False,
            "changed": False,
            "dismissed": True,
        },
    )

    with (
        patch(
            "core.action_executor.configure_damage_slider",
            return_value=damage,
        ) as configure_damage,
        patch(
            "core.action_executor.configure_orb_distance",
            return_value=orb,
        ) as configure_orb,
    ):
        execute_actions(
            object(),
            [
                {
                    "type": "damage_slider_configure",
                    "mode": "enforce",
                    "value": "1E2%",
                },
                {
                    "type": "orb_distance_configure",
                    "mode": "enforce",
                    "range_basis": "98.38m",
                    "extra": "87.16m",
                    "workshop": "80.37m",
                },
            ],
            ctx,
        )

    configure_damage.assert_called_once_with("1E2%", mode="observe")
    configure_orb.assert_called_once_with(
        range_basis="98.38m",
        extra="87.16m",
        workshop="80.37m",
        mode="observe",
    )
    variables = ctx.data["mission_vars"]
    assert variables["damage_slider_checked"] is True
    assert variables["orb_distance_checked"] is True
    assert variables["gc_session_preflight_degraded"] is True
    assert variables["gc_session_preflight_disposition"] == "continue_degraded"
    assert variables["gc_session_preflight_failed_checks"] == [
        "damage_slider",
        "orb_distance",
    ]
    assert variables["gc_running_configuration_degradation"][
        "failed_checks"
    ] == ["damage_slider", "orb_distance"]
    assert variables["gc_session_preflight_evidence"]["valid"] is False
    assert variables["gc_session_preflight_evidence"]["failed_checks"] == [
        "damage_slider",
        "orb_distance",
    ]


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


def test_target_priority_attachment_consumes_attachment_save_carrier():
    class AttachmentSave:
        def consume(self, check_id):
            assert check_id == "target_priority"
            return list(ORDER)

    class WrongCoordinator:
        def consume(self, _check_id):
            raise AssertionError("attachment must use its exact-bound carrier")

    ctx = MissionContext(
        data={
            "startup_gates_deferred": True,
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_attachment_evidence": AttachmentSave(),
            "player_save_preflight_coordinator": WrongCoordinator(),
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
    assert ctx.data["mission_vars"]["target_priority_evidence"]["source"] == (
        "bound_player_save_preflight"
    )


def test_target_priority_requirement_change_falls_back_without_global_invalidation():
    fallbacks = []

    class BoundSave:
        def consume(self, _check_id):
            return list(reversed(ORDER))

        def fallback_checks(self, reason, *, check_ids):
            fallbacks.append((reason, check_ids))

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

    assert fallbacks == [
        ("target_priority_action_requirement_changed", ("target_priority",))
    ]
    ensure.assert_called_once()
    assert ensure.call_args.kwargs["expected"] == ORDER


def test_target_priority_ui_repair_preserves_other_carried_checks():
    invalidations = []
    verifications = []
    mapping_observations = []
    mapping_windows_closed = []

    class BoundSave:
        def consume(self, _check_id):
            return None

        def invalidate(self, reason):
            invalidations.append(reason)

        def record_ui_verification(self, check_id, *, changed):
            verifications.append((check_id, changed))
            return True

        def record_mapping_observation(self, check_id, evidence):
            mapping_observations.append((check_id, evidence))
            return 1

        def close_mapping_candidate_window(self, reason):
            mapping_windows_closed.append(reason)

        def decision(self, check_id):
            assert check_id == "target_priority"
            return {"disposition": "save_mismatch"}

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )

    def ensure(**kwargs):
        kwargs["initial_evidence_observer_fn"](tuple(reversed(ORDER)))
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

    assert invalidations == []
    assert mapping_windows_closed == ["target_priority_repair_started"]
    assert verifications == [("target_priority", True)]
    assert len(mapping_observations) == 1
    check_id, mapping_evidence = mapping_observations[0]
    assert check_id == "target_priority"
    assert mapping_evidence["canonical_values"] == list(reversed(ORDER))
    assert mapping_evidence["locator_values"] == {
        f"rank:{index}": value
        for index, value in enumerate(reversed(ORDER))
    }
    assert mapping_evidence["complete"] is True
    assert mapping_evidence["pre_mutation"] is True
    assert ctx.data["mission_vars"]["target_priority_evidence"] == {
        "source": "ui",
        "checked": True,
        "valid": True,
        "status": "ui_verified_repair",
        "changed": True,
        "save_disposition": "save_mismatch",
    }


def test_target_priority_save_mismatch_ui_match_fails_as_contradiction():
    verifications = []

    class BoundSave:
        def consume(self, _check_id):
            return None

        def record_ui_verification(self, check_id, *, changed):
            verifications.append((check_id, changed))
            return False

        def decision(self, _check_id):
            return {"disposition": "save_mismatch"}

    ctx = MissionContext(
        data={
            "mission_vars": {"last_detection_state": "RUNNING"},
            "player_save_preflight_coordinator": BoundSave(),
        }
    )

    with patch(
        "core.action_executor.ensure_target_priority_order",
        return_value=True,
    ):
        execute_actions(
            None,
            [{"type": "target_priority_ensure", "order": list(ORDER)}],
            ctx,
            action_guard_fn=lambda: True,
        )

    assert verifications == [("target_priority", False)]
    assert ctx.data["mission_vars"]["target_priority_checked"] is False
    assert ctx.data["mission_vars"]["target_priority_evidence"]["status"] == (
        "contradiction"
    )


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


def test_changed_observed_module_carry_falls_back_before_session_use():
    fallbacks = []

    class BoundSave:
        def consume(self, _check_id):
            return {
                **TOURNAMENT_VARIATION,
                "armor_assist": "Anti-Cube Portal",
            }

        def fallback_checks(self, reason, *, check_ids):
            fallbacks.append((reason, check_ids))

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
    assert fallbacks == [
        ("module_boundary_requirement_changed", ("modules",))
    ]


def test_changed_later_lock_requirement_falls_back_only_that_check():
    fallbacks = []

    class BoundSave:
        def consume(self, check_id):
            assert check_id == "free_upgrade_locks"
            return list(FARM_FREE_UPGRADE_LOCKS)

        def fallback_checks(self, reason, *, check_ids):
            fallbacks.append((reason, check_ids))

    setup = {
        "free_upgrade_locks": {
            "status": "save_match",
            "source": "player_save_preflight",
            "required": ["different lock"],
        }
    }

    bound = _bind_save_backed_home_evidence(setup, BoundSave())

    assert "free_upgrade_locks" not in bound
    assert fallbacks == [
        (
            "free_upgrade_lock_boundary_requirement_changed",
            ("free_upgrade_locks",),
        )
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


def test_ui_only_configuration_repairs_preserve_remaining_carried_checks():
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

    assert invalidations == []
