from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest

from core.player_save import SaveCheckEvidence
from core.player_save_acquisition import (
    PlayerSaveAcquisitionBundle,
    PlayerSaveAcquisitionStatus,
    PlayerSaveAcquisitionType,
    PlayerSaveTargetBinding,
)
from core.player_save_setup_capture import (
    SetupCaptureError,
    module_preset_source_from_capture,
    project_forced_save_setup,
    strategy_source_from_capture,
)
from core.control_model import validate_setup_capture_preview
from core.strategy_authoring import FARM_SETTING_REGISTRY
from core.strategy_authoring import fingerprint_document
from core.strategy_profiles import (
    StrategyProfileConflictError,
    StrategyProfileStore,
)


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
TARGET_PRIORITY = [
    "Fleets",
    "Boss",
    "Elites",
    "In Spotlight",
    "Tank",
    "Closest (Default)",
    "Ranged",
    "Protector",
    "Fast",
    "Basic",
]


def _evidence(
    check_id,
    value,
    *,
    status="observed",
    complete=True,
    reason="",
    authority=None,
    diagnostics=None,
):
    return SaveCheckEvidence(
        check_id=check_id,
        status=status,
        value=value,
        source_fields=(f"field_{check_id}",),
        complete=complete,
        reason=reason,
        authority=authority or {"kind": "matching_value"},
        diagnostics=diagnostics or {},
    )


def _checks():
    initial = {
        setting_id: definition.normalizer(definition.initial_value_factory())
        for setting_id, definition in FARM_SETTING_REGISTRY.items()
    }
    ultimate = initial["ultimate_weapons"]
    primaries = {
        name: {"primary": toggles["primary"]}
        for name, toggles in ultimate.items()
    }
    return {
        **{
            check_id: _evidence(check_id, initial[check_id])
            for check_id in (
                "cards_deck",
                "card_recharge_modes",
                "workshop_preset",
                "bots_preset",
                "guardian_chips",
                "auto_pick_perks",
                "perk_bans",
                "perk_auto_pick_order",
            )
        },
        "free_upgrade_locks": _evidence(
            "free_upgrade_locks",
            initial["free_upgrade_locks"],
            diagnostics={
                "unmanaged_locks": [],
                "unmapped_locked_slot_count": 0,
            },
        ),
        "modules": _evidence("modules", MODULES),
        "target_priority": _evidence(
            "target_priority",
            TARGET_PRIORITY,
            authority={"kind": "complete_order", "values": TARGET_PRIORITY},
        ),
        "ultimate_weapon_primaries": _evidence(
            "ultimate_weapon_primaries",
            primaries,
            authority={
                "kind": "all_named_primary_on",
                "names": list(primaries),
            },
        ),
        "poison_swamp_stun": _evidence(
            "poison_swamp_stun",
            "off",
            authority={"kind": "allowed_values", "values": ["on", "off"]},
        ),
        "spotlight_missiles": _evidence(
            "spotlight_missiles",
            "on",
            authority={"kind": "allowed_values", "values": ["on"]},
        ),
        "damage_slider": _evidence(
            "damage_slider",
            None,
            status="unmapped",
            complete=False,
            reason="damageSlider is not mapped",
        ),
        "orb_distance": _evidence(
            "orb_distance",
            None,
            status="unmapped",
            complete=False,
            reason="orb distance is not stored",
        ),
        "perk_first_choice": _evidence(
            "perk_first_choice", "perk_wave_requirement"
        ),
    }


def _acquisition(
    *,
    checks=None,
    validated_checks=None,
    acquisition_type=PlayerSaveAcquisitionType.FORCED_SERIALIZATION,
):
    captured = datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc)
    selected_checks = checks or _checks()
    snapshot = SimpleNamespace(
        mapping_id="data-9-game-1073",
        mapping_maturity="candidate",
        validated_checks=tuple(validated_checks or selected_checks),
        shape_valid=True,
        checks=selected_checks,
    )
    return PlayerSaveAcquisitionBundle(
        acquisition_type=acquisition_type,
        status=PlayerSaveAcquisitionStatus.COMPLETE,
        reason="captured",
        binding=PlayerSaveTargetBinding(target="private-target", generation=4),
        acquisition_started_at=captured - timedelta(seconds=1),
        captured_at=captured,
        acquisition_completed_at=captured + timedelta(seconds=1),
        transport_stable=True,
        snapshot=snapshot,
    )


def _runtime_bound_capture(acquisition):
    capture = project_forced_save_setup(acquisition)
    capture["workflow_binding"] = {
        "schema_version": 1,
        "game_state": "home_new_battle",
        "runtime_session_fingerprint": hashlib.sha256(
            b"thetower-test-runtime"
        ).hexdigest(),
        "activity_scope_fingerprint": hashlib.sha256(
            b"thetower-test-scope"
        ).hexdigest(),
        "target_generation_fingerprint": acquisition.binding.fingerprint,
        "active_round_identity_fingerprint": None,
    }
    capture["capture_origin"] = {
        "schema_version": 1,
        "acquisition_source": "new_setup_capture_refresh",
        "source_manual_control_fingerprint": None,
    }
    return capture


def test_forced_save_capture_reuses_authoring_normalizers_and_preserves_unresolved():
    capture = project_forced_save_setup(_acquisition())

    assert capture["schema_version"] == 1
    assert capture["status"] == "partial"
    assert capture["settings"]["modules"] == {"local": MODULES}
    assert capture["settings"]["target_priority"] == {
        "local": TARGET_PRIORITY
    }
    assert capture["settings"]["ultimate_weapons"]["Poison Swamp"][
        "stun"
    ] == "off"
    assert capture["settings"]["ultimate_weapons"]["Spotlight"][
        "missiles"
    ] == "on"
    unresolved = {
        item["setting_id"]: item for item in capture["unresolved"]
    }
    assert unresolved["damage_slider"]["reason"] == "damageSlider is not mapped"
    assert unresolved["orb_distance"]["reason"] == "orb distance is not stored"
    assert unresolved["perk_first_choice"]["observed_value"] == (
        "perk_wave_requirement"
    )
    assert capture["saving_activates_strategy"] is False
    assert capture["publication_activates_strategy"] is False
    assert "private-target" not in str(capture)


def test_capture_preserves_values_the_existing_farm_schema_cannot_represent():
    checks = _checks()
    checks["auto_pick_perks"] = _evidence(
        "auto_pick_perks",
        False,
        authority={"kind": "exact_values", "values": [True]},
    )
    checks["poison_swamp_stun"] = _evidence("poison_swamp_stun", "on")

    capture = project_forced_save_setup(_acquisition(checks=checks))

    assert "auto_pick_perks" not in capture["settings"]
    assert "ultimate_weapons" not in capture["settings"]
    unresolved = {
        item["setting_id"]: item for item in capture["unresolved"]
    }
    assert unresolved["auto_pick_perks"]["observed_value"] is False
    assert unresolved["ultimate_weapons"]["observed_value"]["Poison Swamp"][
        "stun"
    ] == "on"


def test_candidate_mapping_cannot_capture_a_check_outside_validation_allowlist():
    checks = _checks()
    validated = set(checks) - {"modules"}

    capture = project_forced_save_setup(
        _acquisition(checks=checks, validated_checks=validated)
    )

    assert "modules" not in capture["settings"]
    item = next(
        item for item in capture["unresolved"] if item["setting_id"] == "modules"
    )
    assert "validation allowlist" in item["reason"]


@pytest.mark.parametrize(
    "diagnostics",
    (
        {},
        {"unmanaged_locks": ["unknown-slot"], "unmapped_locked_slot_count": 0},
        {"unmanaged_locks": [], "unmapped_locked_slot_count": 1},
    ),
)
def test_capture_never_claims_free_upgrade_locks_without_complete_diagnostics(
    diagnostics,
):
    checks = _checks()
    checks["free_upgrade_locks"] = _evidence(
        "free_upgrade_locks",
        FARM_SETTING_REGISTRY["free_upgrade_locks"].normalizer(
            FARM_SETTING_REGISTRY["free_upgrade_locks"].initial_value_factory()
        ),
        diagnostics=diagnostics,
    )

    capture = project_forced_save_setup(_acquisition(checks=checks))

    assert "free_upgrade_locks" not in capture["settings"]
    unresolved = next(
        item
        for item in capture["unresolved"]
        if item["setting_id"] == "free_upgrade_locks"
    )
    assert "outside the managed Farm lock mapping" in unresolved["reason"]


def test_capture_rejects_cached_or_natural_save_evidence():
    with pytest.raises(SetupCaptureError) as exc_info:
        project_forced_save_setup(
            _acquisition(
                acquisition_type=PlayerSaveAcquisitionType.PASSIVE_STABLE_READ
            )
        )

    assert exc_info.value.code == "setup_capture_requires_forced_serialization"


def test_capture_builds_normal_strategy_source_and_existing_module_selector():
    capture = project_forced_save_setup(_acquisition())

    source = strategy_source_from_capture(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        base={"id": "farm_base", "revision": 2},
    )

    assert source["schema_version"] == 3
    assert source["settings"]["modules"] == {
        "policy": "enforce",
        "value": {"local": MODULES},
    }
    assert source["base"] == {"id": "farm_base", "revision": 2}
    assert module_preset_source_from_capture(capture) == {"local": MODULES}


def test_captured_strategy_draft_is_atomic_reviewable_and_never_published(tmp_path):
    profile_directory = tmp_path / "profiles"
    store = StrategyProfileStore(profile_directory=profile_directory)
    capture = _runtime_bound_capture(_acquisition())

    reviewed = store.review_captured_strategy_draft(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        expected_capture_fingerprint=fingerprint_document(capture),
    )

    saved = store.save_captured_strategy_draft(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        expected_capture_fingerprint=fingerprint_document(capture),
        expected_review_fingerprint=reviewed["review_fingerprint"],
    )

    assert saved["source"]["schema_version"] == 3
    assert saved["review"]["unresolved"] == capture["unresolved"]
    assert saved["review"]["captured_vs_base"]["change_count"] > 0
    assert saved["review"]["saving_activates_strategy"] is False
    assert saved["review"]["publication_activates_strategy"] is False
    assert saved["capture"]["capture_origin"] == {
        "schema_version": 1,
        "acquisition_source": "new_setup_capture_refresh",
        "source_manual_control_fingerprint": None,
    }
    assert "captured_farm" not in store.strategy_ids()
    assert not (profile_directory / "captured_farm.profile.yaml").exists()

    reopened = StrategyProfileStore(profile_directory=profile_directory)
    catalog = reopened.captured_strategy_draft_catalog()
    assert catalog["errors"] == []
    assert catalog["items"] == [
        {
            "id": "captured_farm",
            "display_name": "Captured Farm",
            "tier": 19,
            "saved_at": saved["saved_at"],
            "draft_fingerprint": saved["draft_fingerprint"],
            "capture_fingerprint": saved["capture_fingerprint"],
            "acquisition_source": "new_setup_capture_refresh",
            "unresolved_count": 3,
            "published": False,
            "selected": False,
            "queued": False,
        }
    ]
    assert reopened.captured_strategy_draft("captured_farm") == saved
    assert reopened.authoring_catalog()["captured_drafts"]["items"] == (
        catalog["items"]
    )


def test_capture_preview_validator_rejects_cached_or_unbound_claims():
    acquisition = _acquisition()
    capture = _runtime_bound_capture(acquisition)

    assert validate_setup_capture_preview(capture) == capture

    cached = {**capture, "acquisition": dict(capture["acquisition"])}
    cached["acquisition"]["type"] = "passive_stable_read"
    assert validate_setup_capture_preview(cached) is None

    wrong_binding = {
        **capture,
        "workflow_binding": {
            **capture["workflow_binding"],
            "target_generation_fingerprint": "0" * 64,
        },
    }
    assert validate_setup_capture_preview(wrong_binding) is None


def test_captured_strategy_draft_and_module_preset_use_existing_save_as_new_owners(
    tmp_path,
):
    store = StrategyProfileStore(profile_directory=tmp_path / "profiles")
    capture = _runtime_bound_capture(_acquisition())

    preset = store.create_module_preset(
        "captured_modules",
        "Captured Modules",
        module_preset_source_from_capture(capture),
    )
    assert preset["definition"] == MODULES
    assert "captured_farm" not in store.strategy_ids()

    reviewed = store.review_captured_strategy_draft(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        expected_capture_fingerprint=fingerprint_document(capture),
    )
    store.save_captured_strategy_draft(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        expected_capture_fingerprint=fingerprint_document(capture),
        expected_review_fingerprint=reviewed["review_fingerprint"],
    )
    with pytest.raises(StrategyProfileConflictError):
        store.save_captured_strategy_draft(
            capture,
            strategy_id="captured_farm",
            display_name="Captured Farm",
            tier=19,
            expected_capture_fingerprint=fingerprint_document(capture),
            expected_review_fingerprint=reviewed["review_fingerprint"],
        )
    with pytest.raises(StrategyProfileConflictError):
        store.save_captured_strategy_draft(
            capture,
            strategy_id="another_capture",
            display_name="Another Capture",
            tier=19,
            expected_capture_fingerprint="0" * 64,
        )
    assert "captured_farm" not in store.strategy_ids()


def test_captured_strategy_base_is_comparison_only_and_unresolved_stays_explicit(
    tmp_path,
):
    store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        base_directory=tmp_path / "bases",
    )
    base = store.publish_base(
        {
            "id": "farm_base",
            "display_name": "Farm Base",
            "family": "farm",
            "settings": {
                "damage_slider": {
                    "policy": "enforce",
                    "value": "1e-19",
                }
            },
        }
    )
    capture = _runtime_bound_capture(_acquisition())

    review = store.review_captured_strategy_draft(
        capture,
        strategy_id="captured_farm",
        display_name="Captured Farm",
        tier=19,
        base={"id": "farm_base", "revision": 1},
        expected_capture_fingerprint=fingerprint_document(capture),
    )

    assert "base" not in review["source"]
    assert "damage_slider" not in review["source"]["settings"]
    assert review["captured_vs_base"]["base"] == {
        "id": "farm_base",
        "revision": 1,
    }
    assert any(
        item["setting_id"] == "damage_slider"
        for item in review["captured_vs_base"]["changed"]
    )
    assert any(
        item["setting_id"] == "damage_slider"
        for item in review["unresolved"]
    )
    assert base["snapshot"]["revision"] == 1


def test_capture_review_treats_equivalent_preset_and_unordered_values_as_same_setup(
    tmp_path,
):
    store = StrategyProfileStore(
        profile_directory=tmp_path / "profiles",
        base_directory=tmp_path / "bases",
    )
    capture = _runtime_bound_capture(_acquisition())
    guardian_chips = list(capture["settings"]["guardian_chips"])
    perk_bans = list(capture["settings"]["perk_bans"])
    store.publish_base(
        {
            "id": "semantic_base",
            "display_name": "Semantic Base",
            "family": "farm",
            "settings": {
                "modules": {
                    "policy": "enforce",
                    "value": {"preset": "farm_standard"},
                },
                "guardian_chips": {
                    "policy": "enforce",
                    "value": list(reversed(guardian_chips)),
                },
                "perk_bans": {
                    "policy": "enforce",
                    "value": list(reversed(perk_bans)),
                },
            },
        }
    )

    review = store.review_captured_strategy_draft(
        capture,
        strategy_id="captured_semantic",
        display_name="Captured Semantic",
        tier=19,
        base={"id": "semantic_base", "revision": 1},
        expected_capture_fingerprint=fingerprint_document(capture),
    )

    changed = {
        item["setting_id"]
        for item in review["captured_vs_base"]["changed"]
    }
    representation_only = {
        item["setting_id"]
        for item in review["captured_vs_base"]["provenance_changed"]
    }
    assert "modules" not in changed
    assert "guardian_chips" not in changed
    assert "perk_bans" not in changed
    assert {"modules", "guardian_chips", "perk_bans"} <= (
        representation_only
    )


def test_captured_strategy_catalog_reports_malformed_draft_filenames(tmp_path):
    directory = tmp_path / "profiles" / "captured_drafts"
    directory.mkdir(parents=True)
    (directory / "BAD.captured-strategy-draft.yaml").write_text(
        "not: a valid draft\n",
        encoding="utf-8",
    )
    store = StrategyProfileStore(profile_directory=tmp_path / "profiles")

    catalog = store.captured_strategy_draft_catalog()

    assert catalog["items"] == []
    assert catalog["errors"] == [
        {
            "id": "invalid_filename",
            "error": (
                "Captured Strategy draft filename is invalid: "
                "BAD.captured-strategy-draft.yaml"
            ),
        }
    ]
