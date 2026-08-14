import ast
from pathlib import Path

import pytest

from automation.missions.manager import MissionManager
from core.automation_supervisor import AutomationSupervisor
from core.gate_decisions import build_gate_decision_options
from core.runtime_failure_policy import (
    CATASTROPHIC_FAILURE_KINDS,
    RuntimeFailureDisposition,
    RuntimeFailureKind,
    decide_runtime_failure,
)


def test_only_catastrophic_failures_pause_automation():
    decisions = {
        kind: decide_runtime_failure(kind)
        for kind in RuntimeFailureKind
    }

    assert {
        kind
        for kind, decision in decisions.items()
        if decision.disposition is RuntimeFailureDisposition.PAUSE_FOR_SAFETY
    } == CATASTROPHIC_FAILURE_KINDS
    assert all(
        decision.catastrophic == (kind in CATASTROPHIC_FAILURE_KINDS)
        for kind, decision in decisions.items()
    )


@pytest.mark.parametrize(
    "kind",
    sorted(
        set(RuntimeFailureKind) - set(CATASTROPHIC_FAILURE_KINDS),
        key=lambda value: value.value,
    ),
)
def test_recoverable_failure_repairs_only_when_safe_boundary_is_available(kind):
    assert decide_runtime_failure(kind).disposition is (
        RuntimeFailureDisposition.CONTINUE_DEGRADED
    )
    assert decide_runtime_failure(
        kind,
        repair_available=True,
    ).disposition is RuntimeFailureDisposition.REPAIR_NOW


def test_failure_policy_rejects_untyped_callers():
    with pytest.raises(TypeError, match="RuntimeFailureKind"):
        decide_runtime_failure("configuration_mismatch")  # type: ignore[arg-type]


def test_runtime_orchestrator_has_no_generic_pause_or_strategy_gate_calls():
    app_source = (
        Path(__file__).resolve().parents[1] / "core" / "app.py"
    ).read_text(encoding="utf-8")
    called_attributes = {
        node.func.attr
        for node in ast.walk(ast.parse(app_source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }

    assert "persist_state" not in called_attributes
    assert "activate_strategy_gate" not in called_attributes
    assert not hasattr(AutomationSupervisor, "persist_state")


def test_configuration_failures_cannot_authorize_battle_restart():
    options = build_gate_decision_options("modules")
    advisory = build_gate_decision_options("modules", advisory=True)

    assert {option["action"] for option in [*options, *advisory]} <= {
        "retry",
        "waive",
    }
    assert not hasattr(MissionManager, "authorize_session_preflight_restart")
    assert not hasattr(MissionManager, "begin_session_preflight_repair")


def test_runtime_entrypoint_never_installs_a_blocking_failure_prompt():
    main_source = (
        Path(__file__).resolve().parents[1] / "main.py"
    ).read_text(encoding="utf-8")

    assert "gate_decision_prompt" not in main_source
    assert "prompt_for_gate_decision" not in main_source
